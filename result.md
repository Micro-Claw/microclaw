Block 80c implementation handoff

Worktree `/Users/zachcm/Code/microclaw-80c`, branch
`design80/standalone-plugin-capability`, starting commit `58f4c52`.
No new worktree, merge, push, or change to main.

Files and decisions:

- `microclaw/controller.py`: best-effort settings reader using
  `_new_static_java_class(port, "java.lang.reflect.Array")` and `get_length/get`.
  Values remain opaque text; a failed read returns unavailable with its exception
  type/message and no partial settings.
- `microclaw/hooks.py`: read the snapshot once after resolving the autofocus
  method. Passive Z guarding, callback, logging and exception behavior remain.
- `microclaw/tools.py`: carry `autofocus_settings_snapshot` through the existing
  `_adaptive_result` seam for fixed stacks/timelapses, multiposition movies and
  surveys. Inline the accessor, collection drain, static-class wrapper and
  snapshot reader via `inspect.getsource`; wire Studio to `mm.plugins`. Pin the
  portable motion guard to the recorded constructor token. Print recorded/live
  snapshots and flag textual differences without a prompt or refusal. Split
  movies consume their corresponding recorded snapshots. Older records disclose
  absence rather than fabricating settings. Correct the retained analyzer
  refusal to name arbitrary construction and runtime authorization.
- `tests/test_session_script_export.py`: 13 new cases execute actual emitted
  imports/source against a dispatching acquisition fake that invokes the
  post-hardware callback per event. Cover array-shaped names, locale text,
  unavailable snapshots, separate movie records, active/named selection,
  validation, motion authorization, Z envelopes and passive unsafe-Z aborts.
  Update the incident test because its successful plugin call now exports;
  remove the obsolete autofocus refusal from the observer/filter regression.
- `CLAUDE.md`: correct the capability/refusal documentation.
- `tests/test_plugin_hooks.py`: explicitly stub the unavailable settings bridge
  in existing callback-only regression tests, so their old MagicMocks cannot
  launch real bridge threads. Those tests do not exercise property-name reads;
  the new snapshot tests use the array-shaped fake.
- `result.md`: this report.

Judgment call: a separate snapshot reader, leaving `_drain_java_iterable`
unchanged. Collections and arrays have distinct established APIs; this avoids
speculative shape detection in collection validation. The drain is inlined in
exports, so a change there would travel too. This block inlines the array reader
beside it. No `safety.py` change was needed: the live motion gate remains
unchanged, and the new recorded field belongs to the existing tool-result seam.

Timing: one snapshot per hook construction, none in the per-event callback.
Tests measure one names enumeration/static wrapper call and two focus calls for
a two-event movie, and two enumerations for two separately constructed movies.
The baseline constructor made zero snapshot reads. Added bridge traffic is one
static wrapper, one names read, a reflection length read, and index/value reads
per setting. Actual bridge latency and focus convergence were not measured
off-rig; no rig evidence is claimed.

Development corrections: initial focused failures exposed missing `timing`, an
incorrect fake `export_limits` override, and missing motion opt-in in the live
fixture. The export-file run also found obsolete refusal expectations. These
were corrected. Diff review caught and removed an accidental undefined `hook`
reference introduced by a broad replacement in `_hooked_failure_result` before
final verification. The initial export-file run preceded the last three tests.

Pure regressions include the observer/filter test (after deleting obsolete
assertions), hookless hash, shared classification and parse-before-write tests.
No pre-fix failure is claimed for unchanged behavior. The new unsafe/exception
tests exercise newly available standalone execution of unchanged live semantics,
so they are included in the pre-fix run.

Pre-fix procedure: copied the three edited product files to `/tmp/80c-fixed`,
then used `git show 58f4c52:microclaw/<name>` to replace each working file with
its original bytes. These are the only changed product files, so the full
product implementation was at the pre-fix version. The index was not changed.
After the failing run, restored the copies before running the full suite.

```sh
mkdir -p /tmp/80c-fixed
cp microclaw/controller.py microclaw/hooks.py microclaw/tools.py /tmp/80c-fixed/
python3 - <<'PY'
from pathlib import Path
import subprocess
for name in ('controller.py', 'hooks.py', 'tools.py'):
    Path('microclaw', name).write_bytes(subprocess.check_output(['git', 'show', '58f4c52:microclaw/' + name]))
PY
# Run the pre-fix pytest command listed below, then restore:
cp /tmp/80c-fixed/controller.py /tmp/80c-fixed/hooks.py /tmp/80c-fixed/tools.py microclaw/
```

Hash measurement:

```sh
.venv/bin/python - <<'PY'
import hashlib
from pathlib import Path
from tempfile import TemporaryDirectory
from tests.test_session_script_export import export, call, _80B_BASE
with TemporaryDirectory() as directory:
    _, _, source = export(Path(directory), [call('run_multiposition_acquisition', _80B_BASE)])
    print('sha256', hashlib.sha256(source.encode()).hexdigest())
    print('lines', len(source.splitlines()))
PY
```

```text
sha256 9a56d7a9ca1e93e173cb4358712d94f4b837dfcfe7a933010af9f4df033b0826
lines 460
```

`git diff --check`: no output, exit 0.

Validation commands and output tails (all from worktree root):

```sh
.venv/bin/python -m pytest -q tests/test_session_script_export.py -k 80c > /tmp/80c-focused.txt 2>&1
```

```text
3 failed, 7 passed, 235 deselected in 5.80s
```

```sh
.venv/bin/python -m pytest -q tests/test_session_script_export.py > /tmp/80c-export.txt 2>&1
```

```text
4 failed, 241 passed in 99.48s (0:01:39)
```

```sh
.venv/bin/python -m pytest -q tests/test_session_script_export.py -k 80c > /tmp/80c-focused2.txt 2>&1
```

```text
2 failed, 11 passed, 235 deselected in 8.32s
```

```sh
.venv/bin/python -m pytest -q tests/test_session_script_export.py -k 80c > /tmp/80c-focused3.txt 2>&1
```

```text
13 passed, 235 deselected in 5.98s
```

```sh
.venv/bin/python -m pytest -q tests/test_session_script_export.py -k '80c or 80a_incident or observation_and_position_filter or 80b_hookless or never_writes_a_file' > /tmp/80c-finalfocused.txt 2>&1
```

```text
17 passed, 231 deselected in 9.05s
```

```sh
.venv/bin/python -m pytest -q tests/test_session_script_export.py -k '80c or 80a_incident' --tb=short > /tmp/80c-prefix.txt 2>&1
```

```text
14 failed, 234 deselected in 1.42s
```

Verbatim pre-fix failure blocks follow. Captured stderr progress logs
are omitted; pytest's abbreviated representations (including `...`) are
retained literally. All 13 new cases and the changed incident case failed.
The live cases detect the missing recorded field; the exported execution cases
hit the old refusal before they can run. The Z test detects that the old refusal
preempts the source-derived bound check. The separate guard/analyzer tests
isolate the missing method and incorrect refusal reason.

```text
_______________ test_80a_incident_categories_and_selected_subset _______________
tests/test_session_script_export.py:4395: in test_80a_incident_categories_and_selected_subset
    assert result["complete"] is True
E   assert False is True
```

```text
______________ test_80c_live_snapshot_reaches_tool_result[False] _______________
tests/test_session_script_export.py:5130: in test_80c_live_snapshot_reaches_tool_result
    snapshot = result['autofocus_settings_snapshot']
               ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
E   KeyError: 'autofocus_settings_snapshot'
```

```text
_______________ test_80c_live_snapshot_reaches_tool_result[True] _______________
tests/test_session_script_export.py:5130: in test_80c_live_snapshot_reaches_tool_result
    snapshot = result['autofocus_settings_snapshot']
               ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
E   KeyError: 'autofocus_settings_snapshot'
```

```text
_____________ test_80c_export_executes_plugin_and_discloses[same] ______________
tests/test_session_script_export.py:5149: in test_80c_export_executes_plugin_and_discloses
    assert result['complete'], result['not_emitted_calls']
E   AssertionError: [{'tool_use_id': 'run_multiposition_acquisition-id-3', 'tool': 'run_multiposition_acquisition', 'reason': "hook 'autof...plugin' requires Micro-Manager plugin capabilities through the Microclaw controller and has no standalone equivalent"}]
E   assert False
```

```text
___________ test_80c_export_executes_plugin_and_discloses[different] ___________
tests/test_session_script_export.py:5149: in test_80c_export_executes_plugin_and_discloses
    assert result['complete'], result['not_emitted_calls']
E   AssertionError: [{'tool_use_id': 'run_multiposition_acquisition-id-4', 'tool': 'run_multiposition_acquisition', 'reason': "hook 'autof...plugin' requires Micro-Manager plugin capabilities through the Microclaw controller and has no standalone equivalent"}]
E   assert False
```

```text
_____ test_80c_export_executes_plugin_and_discloses[recorded_unavailable] ______
tests/test_session_script_export.py:5149: in test_80c_export_executes_plugin_and_discloses
    assert result['complete'], result['not_emitted_calls']
E   AssertionError: [{'tool_use_id': 'run_multiposition_acquisition-id-5', 'tool': 'run_multiposition_acquisition', 'reason': "hook 'autof...plugin' requires Micro-Manager plugin capabilities through the Microclaw controller and has no standalone equivalent"}]
E   assert False
```

```text
_______ test_80c_export_executes_plugin_and_discloses[live_unavailable] ________
tests/test_session_script_export.py:5149: in test_80c_export_executes_plugin_and_discloses
    assert result['complete'], result['not_emitted_calls']
E   AssertionError: [{'tool_use_id': 'run_multiposition_acquisition-id-6', 'tool': 'run_multiposition_acquisition', 'reason': "hook 'autof...plugin' requires Micro-Manager plugin capabilities through the Microclaw controller and has no standalone equivalent"}]
E   assert False
```

```text
____________ test_80c_export_executes_plugin_and_discloses[absent] _____________
tests/test_session_script_export.py:5149: in test_80c_export_executes_plugin_and_discloses
    assert result['complete'], result['not_emitted_calls']
E   AssertionError: [{'tool_use_id': 'run_multiposition_acquisition-id-7', 'tool': 'run_multiposition_acquisition', 'reason': "hook 'autof...plugin' requires Micro-Manager plugin capabilities through the Microclaw controller and has no standalone equivalent"}]
E   assert False
```

```text
_______________ test_80c_export_preserves_passive_guard[unsafe] ________________
tests/test_session_script_export.py:5187: in test_80c_export_preserves_passive_guard
    assert result['complete'], result['not_emitted_calls']
E   AssertionError: [{'tool_use_id': 'run_multiposition_acquisition-id-8', 'tool': 'run_multiposition_acquisition', 'reason': "hook 'autof...plugin' requires Micro-Manager plugin capabilities through the Microclaw controller and has no standalone equivalent"}]
E   assert False
```

```text
______________ test_80c_export_preserves_passive_guard[exception] ______________
tests/test_session_script_export.py:5187: in test_80c_export_preserves_passive_guard
    assert result['complete'], result['not_emitted_calls']
E   AssertionError: [{'tool_use_id': 'run_multiposition_acquisition-id-9', 'tool': 'run_multiposition_acquisition', 'reason': "hook 'autof...plugin' requires Micro-Manager plugin capabilities through the Microclaw controller and has no standalone equivalent"}]
E   assert False
```

```text
_______________ test_80c_plugin_z_envelope_refuses_before_frames _______________
tests/test_session_script_export.py:5212: in test_80c_plugin_z_envelope_refuses_before_frames
    assert 'recorded stage bounds are incomplete for Z' in result['not_emitted_calls'][0]['reason']
E   assert 'recorded stage bounds are incomplete for Z' in "hook 'autofocus_mm_plugin' requires Micro-Manager plugin capabilities through the Microclaw controller and has no standalone equivalent"
```

```text
________________ test_80c_portable_motion_guard_pins_one_token _________________
tests/test_session_script_export.py:5219: in test_80c_portable_motion_guard_pins_one_token
    namespace['guard'].check_plugin_motion('autofocus:OughtaFocus')
    ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
E   AttributeError: '_RecordedSafetyGuard' object has no attribute 'check_plugin_motion'. Did you mean: 'check_illumination'?
```

```text
___ test_80c_analyzer_refusal_names_arbitrary_construction_and_authorization ___
tests/test_session_script_export.py:5229: in test_80c_analyzer_refusal_names_arbitrary_construction_and_authorization
    assert 'plugins.get_object(classpath)' in reason
E   assert 'plugins.get_object(classpath)' in "hook 'mm_plugin_analyzer' requires Micro-Manager plugin capabilities through the Microclaw controller and has no standalone equivalent"
```

```text
____________ test_80c_split_movies_disclose_each_recorded_snapshot _____________
tests/test_session_script_export.py:5240: in test_80c_split_movies_disclose_each_recorded_snapshot
    assert result['complete'], result['not_emitted_calls']
E   AssertionError: [{'tool_use_id': 'run_multiposition_acquisition-id-10', 'tool': 'run_multiposition_acquisition', 'reason': "hook 'auto...plugin' requires Micro-Manager plugin capabilities through the Microclaw controller and has no standalone equivalent"}]
E   assert False
```
Additional verification:

```sh
.venv/bin/python -m pytest -q tests/test_plugin_hooks.py tests/test_session_script_export.py -k 'TestMMAutofocusPluginHook or 80c' > /tmp/80c-warningfix.txt 2>&1
```

```text
17 passed, 248 deselected in 5.77s
```

The existing callback tests now explicitly stub the unavailable bridge and
produce no new thread warnings. Their assertions are unchanged regressions.

```sh
.venv/bin/python -m pytest -q tests/test_session_script_export.py -k 80c_plugin_z_envelope > /tmp/80c-zwalk.txt 2>&1
```

```text
1 passed, 247 deselected in 1.96s
```

The final Z test removes nominal Z from every seed, proving the bound requirement
comes from the inlined hook's `check_z` source walk. It executes the refused
script too and observes no backend construction or frames. This strengthening
was made after the final full run had collected tests, so the targeted result
above verifies the final form; its pre-fix result is recorded below.

```sh
.venv/bin/python -m pytest -q > /tmp/80c-suite.txt 2>&1
```

```text
4 failed, 2959 passed, 99 skipped, 6 warnings in 204.52s (0:03:24)
```

This first full run found the four sandbox artefacts named below and three new
bridge-thread warnings caused by the pre-existing callback-test MagicMocks.
The fixture correction and warning-free targeted run above resolve those three
new warnings. Baseline warnings are not findings.

Final full suite:

```sh
.venv/bin/python -m pytest -q > /tmp/80c-suite-final.txt 2>&1
```

```text
FAILED tests/test_bridge_check.py::test_tcp_listener_without_zmq_handshake_is_not_ready
FAILED tests/test_skills.py::test_built_wheel_contains_the_source_tree_skill_catalog
FAILED tests/test_webserve.py::test_browser_opens_only_once_the_port_accepts
FAILED tests/test_webserve.py::test_browser_opener_gives_up_instead_of_hanging
4 failed, 2959 passed, 99 skipped, 3 warnings in 208.22s (0:03:28)
```

Baseline reconciliation: 2950 + 13 new cases = 2963. Of those, 2959 passed
and four encountered the established sandbox limitations; no product-test
failure remains. The 99 skips are unchanged. Three benign baseline warnings
were emitted; the fourth baseline warning (`mmpycorex`'s compile-time syntax
warning) did not recur with its bytecode cache populated. No new warning remains.

Sandbox artefacts, not product defects:

- `tests/test_bridge_check.py::test_tcp_listener_without_zmq_handshake_is_not_ready`
- `tests/test_webserve.py::test_browser_opens_only_once_the_port_accepts`
- `tests/test_webserve.py::test_browser_opener_gives_up_instead_of_hanging`

All three fail on `bind(("127.0.0.1", 0))` with the exact text:

```text
E       PermissionError: [Errno 1] Operation not permitted
```

- `tests/test_skills.py::test_built_wheel_contains_the_source_tree_skill_catalog`
  fails in its isolated `pip wheel . --no-deps --wheel-dir ...` subprocess,
  with `subprocess.CalledProcessError` / `returned non-zero exit status 1`.
  This is the known sandbox wheel-build artefact identified in the assignment.
  Pytest's traceback does not expose the subprocess's captured stderr, so no
  more specific cause is claimed. The test needs the coordinator's outside-
  sandbox recheck; it was not modified, skipped or marked xfail.

The required suite itself invokes that isolated wheel subprocess. No package
installation/resolution command was independently run, and no network workaround
or escalation was attempted. The final suite repetition verified the fixture
warning fix, not a network retry workaround. All four environment-dependent
checks remain for the coordinator to verify outside the sandbox. The demo rig
replay and real focus convergence were not available here and are not claimed.

Final-form Z source-walk pre-fix check used the same `git show` replacement and
copy-back procedure above, with:

```sh
.venv/bin/python -m pytest -q tests/test_session_script_export.py -k 80c_plugin_z_envelope --tb=short > /tmp/80c-zwalk-prefix.txt 2>&1
```

Verbatim output:

```text
F                                                                        [100%]
=================================== FAILURES ===================================
_______________ test_80c_plugin_z_envelope_refuses_before_frames _______________
tests/test_session_script_export.py:5216: in test_80c_plugin_z_envelope_refuses_before_frames
    assert 'recorded stage bounds are incomplete for Z' in result['not_emitted_calls'][0]['reason']
E   assert 'recorded stage bounds are incomplete for Z' in "hook 'autofocus_mm_plugin' requires Micro-Manager plugin capabilities through the Microclaw controller and has no standalone equivalent"
=========================== short test summary info ============================
FAILED tests/test_session_script_export.py::test_80c_plugin_z_envelope_refuses_before_frames
1 failed, 247 deselected in 0.25s
```

After restoring the final implementation:

```sh
.venv/bin/python -m pytest -q tests/test_session_script_export.py -k '80c or 80b_hookless or never_writes_a_file' > /tmp/80c-restored.txt 2>&1
```

```text
15 passed, 233 deselected in 7.40s
```

This verifies all final 80c tests, the exact hookless bytes, and parse-before-write
again on the restored product tree. Final `git diff --check`: exit 0, no output.
