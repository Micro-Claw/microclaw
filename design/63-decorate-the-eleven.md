# Every tool must be able to appear in an exported script

## Problem

`export_session_script` dispatches on three marker attributes
(`_microclaw_emitter`, `_microclaw_emits_nothing`, `_microclaw_refusal_reason`).
A tool carrying none of them collects the **default refusal**, which plants

```
# NOT EMITTED: <tool> — no standalone emitter has been implemented for this tool
raise RuntimeError('NOT EMITTED: <tool> — …')
```

into every script that recorded it, halting the run at that line. That mechanism
is correct — a plausible fabrication of a step is the defect it prevents — but it
has killed three blocks' own gate scripts (43h `generate_and_save_hook`, 47
`set_roi`/`clear_roi`, 52a `move_named_stage`), each time discovered on a rig.

Measured over `TOOL_REGISTRY` 2026-08-29 (81 tools): **eleven undecorated**, the
same eleven named in design/35's carried-forward register and in `CLAUDE.md`.
Several are ordinary in a session, so an export that dies is the likely case.

The register's own instruction: *"They fall into three groups and each needs a
decision, not a sweep of `@emits_nothing`."* This document makes those eleven
decisions.

## Decision

| Tool | Decoration | Why |
|---|---|---|
| `calibrate_snr_threshold` | `@emits_nothing` | Its docstring is the argument: *"No frames are acquired."* Pure computation over confirmed control logs into a threshold file. |
| `verify_emu_laser_power_calibration` | `@emits_nothing` | Arithmetic over two operator-supplied observations against the EMU affine. Reads a config entry; writes no device. Its only effect is a session-cache gate on `set_emu_laser_power_percentage`, which is microclaw state and not hardware routine. |
| `export_dataset_as_tiff` | `@emits_nothing` | Offline transform of an already-saved dataset. Same class as `run_analysis_on_saved_dataset`, `@emits_nothing` since 43i. No dose, no hardware, and no acquisition step depends on its output. |
| `shutter_declared_illumination` | `@emits` | A real hardware write per declared shutter. Emit the writes **the session actually performed**, from the recorded result — never re-derived from the safety config, which the standalone script does not have. |
| `set_emu_laser_power_percentage` | `@emits` | A real illumination write. Emit `core.set_property(device, property, raw_value_written)` — the value read back after the write — with the recorded requested/effective percent in a comment beside it, because in an exported script that comment is the only disclosure of the dose. The percent→raw conversion is rig calibration and is **not** re-computed in the script. |
| `find_features` | `@emits` | A snap (a dose) plus a pure-numpy-ish detection. Same shape as `_emit_snap_and_analyze`: emit `snap_to_numpy(mm)` then a `detect_features(...)` inlined by `_analysis_source` with `inspect.getsource`. |
| `center_feature` | `@emits` | A closed loop that snaps and **moves the stage** repeatedly. 43h's rule applies: *emit the program, not the trace* — the seed, the affine, the tolerance and the loop, not the moves one run happened to make. |
| `run_multiposition_with_autofocus` | `@emits` | A deprecated forwarding wrapper. Its emitter builds the forwarded call and **delegates to `_emit_multiposition`**, so the wrapper can never drift from the tool it forwards to. Today that delegation yields `CannotEmit("hooked acquisition ('autofocus_per_position') …")` — a truthful, specific refusal replacing the generic one, and it improves automatically when the hooked route does. |
| `calibrate_stage_to_camera` | `@refuses` | The solve and its knowledge-base cache are `microclaw.calibration` (`solve_affine`, `save_affine`, `affine_version_key`); inlining would not be standalone. Same reason the offline mosaic refuses. |
| `snap_to_album` | `@refuses` | The Album is an MMStudio datastore. The exported script builds `core = Core()` and has no `studio`; emitting a bare `core.snap_image()` would fabricate a different operation — a display-only snap that joins no album and saves nothing. |
| `run_mda` | `@refuses` | MMStudio owns the settings, and the tool's contract is a preview token hashed over settings read *immediately before* the run. A standalone script has neither the studio nor the settings, and re-running "whatever the MDA dialog holds now" is not the recorded acquisition. |

Net: 3 `@emits_nothing`, 4 `@emits`, 4 `@refuses`, and **zero undecorated tools**.

### Two results must record what the tool wrote

An emitter may only render what the record contains. Two of the four emitters
need a result the tool does not currently report, and both additions are honest
disclosure rather than plumbing:

- `shutter_declared_illumination` reports `shuttered` as `"device.property"`
  strings. `SafetyGuard.shutter_all` must report the **triples it wrote**
  (`device`, `property`, `value`), and the tool must pass them through.
- `set_emu_laser_power_percentage` records `raw_value_written` but not the
  `device`/`property` it wrote it to. Add both.

`center_feature` needs the affine it used; report the coefficients it applied.
A tool that reports a number should report where it sent it.

### What is deliberately not done

- **`run_multiposition_with_autofocus` is not deleted.** `CLAUDE.md`'s
  no-legacy-anchoring rule argues for removing a deprecated forwarding wrapper
  outright, but that is a different change with its own callers and its own
  gate. Decorating it costs four lines and delegates.
- **`_emit_multiposition` is not taught to emit hooked runs.** 43j gave
  `run_timelapse`/`run_zstack` an adaptive emitter when a hook is attached;
  `run_multiposition_acquisition` still refuses one. That asymmetry is
  pre-existing, is the reason the wrapper refuses, and goes to the register.

## Required tests

1. **The sweep is a test, not a runbook step.** A test over `TOOL_REGISTRY`
   asserting every tool carries exactly one of the three markers, listing the
   offenders by name on failure. This is the guard that stops a twelfth tool
   from shipping undecorated, and it is what every previous block lacked.
2. **Each refusal is asserted on its own reason**, not on the fact that a
   refusal happened — a default-refusal regression would pass an
   "is there a `NOT EMITTED`" test.
3. **`test_emitted_inline_defines_every_name_it_uses` must cover
   `detect_features`** and whatever it reaches (`skimage.feature.blob_log`,
   `scipy.ndimage.center_of_mass` are external imports the emitted header must
   carry when, and only when, a detection is emitted).
4. **Execute the emitted source, do not merely compile it** (52b). At least
   `find_features`, `center_feature` and `shutter_declared_illumination` get a
   test that `exec`s the emitted script against fakes and asserts the writes and
   snaps that reach the fake core.
5. **The two enriched results are asserted at the tool**, so an emitter can
   never be the only reason a field exists.

## Gate — one program, plus one driven session

Offline-scorable except for the EMU pair. `design/63-block63a-demo-gate.py`,
run against a bridge-shaped fake selftest on **both** trees before it ships
(design/59: `list()` over a Core collection passes every `MagicMock`).

- **G0** the sweep: zero undecorated over the installed build.
- **G1** a driven demo session that snaps, finds features, exports a dataset to
  TIFF and shutters declared illumination, then exports; the emitted script
  carries **zero `NOT EMITTED`**, compiles, and **runs standalone with
  microclaw closed**.
- **G2** the refusal limbs: a session recording `snap_to_album` and
  `run_mda` emits each tool's **specific** reason, and neither carries the
  default sentence.
- **G3** M5 only, and optional: the EMU pair. Ships rig-ungated with a reason
  if no M5 trip is available.

A limb that could not run its mechanism reports **NOT EXERCISED**, which is
never a pass, and the program owns its own log and exits nonzero.

## Run ledger

| Block | Branch | Start commit | Implementation | Rig evidence | Merge |
|---|---|---|---|---|---|
| 63a | `design63/decorate-the-eleven` | `9b058c3` (2026-08-29), worktree `../microclaw-63a` | `ec26f21` (1 Codex start turn + 1 revision turn). Coordinator suite reproduced at both turns: 2,509 → **2,512 passed / 99 skipped / 2,611 collected** (`main` was 2,594). Five findings returned, one blocking: the start turn widened `settle_stage_move` to accept an XY tuple so the emitted centring loop could settle — but the live `move_stage_xy` does not settle, it `wait_for_device`s and *reports* the residual, so the emitted script would have raised `StageMoveError` where the session carried on, in a type-switched helper that is inlined into every exported script. **That instruction was the coordinator's error and was withdrawn**; `controller.py` is now byte-identical to `main`. The other four: `detect_features` inlined unconditionally (breaking `_analysis_source`'s stated compute_stats-reachability rule and putting 45 unused lines into every analysis script); a malformed `shuttered` record raising `ValueError` out of `export_session_script`, losing the **whole session's** script rather than one step; an empty shutter emission leaving a bare `# RECORDED TOOL` heading; and `log_path` not reaching the forwarded multiposition call (43j). | Gate pushed at `9127c0f` (runbook pins `ec26f21`). **Selftest run on both trees before pushing: 13/13, and it found two defects in itself first** — `_export_with` inherited the caller's cwd, so `python -c` put the 63a checkout at the head of `sys.path` and the *main*-tree artifact was built by 63a's exporter, turning both discrimination limbs green; and the checkout-guard check ran an interpreter whose microclaw resolved to a different tree, so the guard correctly stayed quiet and the check measured nothing. **Demo round 1, 2026-08-30: all six limbs PASS** on the installed build (`env-a`) — 81 tools / 0 undecorated / 23 emits + 54 emits_nothing + 4 refuses, 6 of the eleven reached. **The corroboration is better than the verdict**: the standalone run's own capture is one line (`EXIT=0`), but it wrote `timelapse_2` at 07:57 — one frame, `{"time":0}`, a 532,637-byte stack against the live run's 532,638 and an NDTiff index differing in a single byte of metadata length. The emitted `detect_features` therefore ran standalone with no `NameError`, which is the block-13/41b failure class this export exists to prevent. **`calibrate_stage_to_camera` failed on the demo camera as predicted and still emitted its refusal**, proving on real data that a `@refuses` tool refuses before the exporter reads whether the call succeeded. **Three honest qualifications, none a failure.** (1) The demo config declares no illumination (`attempted: []`), so `shutter_declared_illumination` emitted R4's comment and its *write* path was never exercised on a rig — G3 counted it as "emitting", which overstates, so G4 now names any tool that emitted only a comment and the amended gate reports exactly that on this session. (2) `find_features` returned `n_spots: 0, snr: 0.96`: execution proven, detection content not. (3) `run_mda` is excluded from the Phase-1 authorization map at code level (`RigAuthorizationError`), so the runbook's instruction to approve its confirmation described a prompt that cannot be reached; corrected. **One runbook defect, and it is 52c recurring**: Turn 5 was sent with `PASTE_WORK_PATH` unsubstituted and the export landed in `D:\Code\microclaw\PASTE_WORK_PATH\full.py` inside the operator's checkout. A prose warning about a placeholder did not prevent it; Step 2 now prints its turns with `$work` already interpolated. Gate fixes at `58963b8`, selftest 13/13 after them. | |

## Carried-forward register

| Finding | Source | Disposition |
|---|---|---|
| `run_multiposition_acquisition` refuses any non-observation hook, where `run_timelapse`/`run_zstack` emit the adaptive program (43j) | this block | open — no block |
| `run_multiposition_with_autofocus` is a deprecated forwarding wrapper that should probably be deleted | this block | open — no block |
