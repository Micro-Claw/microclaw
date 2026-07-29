# Designs 26, 29, 32, and 33 — implementation checklist

Last reviewed: 2026-07-23

This is the implementation order and handoff record for the four designs. It is
deliberately a checklist rather than another design authority. When this file and a
design disagree, stop and reconcile the design before assigning more implementation.

## How to use this file

One **coordinator agent** owns this checklist. A different **implementation agent**
owns one numbered implementation block at a time.

- [ ] The coordinator starts each block from updated `main`, creates the named branch,
      and records the starting commit below.
- [ ] The coordinator gives the implementer only that block, the named design sections,
      and the acceptance evidence required by the block.
- [ ] The implementer codes, tests, commits, and returns the commit, test output, risks,
      and any design assumptions that proved false. It does not merge its own branch.
- [ ] The coordinator reviews the diff and evidence. If a rig gate is listed, it stops
      the implementation flow and runs/assigns the spike before approving a merge.
- [ ] The coordinator merges only a green block, updates `main`, and records the merge.
- [ ] After every merge, the coordinator performs the block's **post-merge design gate**.
      If documentation must change, create and merge a small docs branch before starting
      the next implementation block.
- [ ] Do not combine blocks merely because they touch the same files. The branch/merge
      boundaries are safety and rollback boundaries.

Progress markers: `[ ]` not started, `[-]` active, `[x]` complete, `[!]` blocked.

### Run ledger

| Block | Branch | Start commit | Implementation commit/PR | Rig evidence | Merge commit | Design reconciliation |
|---|---|---|---|---|---|---|
| 0 | — | c90d738 | baseline: 771 passed / 98 skipped (2026-07-22) | n/a | — | Run A located; NDTiff fixtures deferred to Block 8 |
| — | — | — | **Baseline drift note:** the counts in this table are per-block snapshots, not a running total. `main` was 841/98 before Block 3b (not the 816 recorded at Block 2, which predates Block 3's tests). Re-measure `main` before judging a block's test output. | | | |
| 1 | `design32/config-hardening` | c90d738 | 967ab5d | n/a | 56ed945 | Gate done: config matches Finding 1; non-numeric-write fail-closed change noted in design/32 (docs merge c86af6d) |
| 2 | `design32/versioned-safety-schema` | 252a4cc | d5aa5c4 (+fix 12b0677) | rig: starts clean w/ reviewed rig config | b8092c4 | Gate done: API matches design/33 (no change); 1b landed note + required-when-present judgment recorded in design/32 (docs merge 8dd521f) |
| 3 | `design33/core-authorization-map` | c18fa92 | fc346a4 (2 review-fixes) | rig M5: fail-closed+parity, complete map, cat/illum writes pass, PWM/FPGA refused, confirm-gate fires | 0124ffd | Gate done: design/33 Phase-1 landed note + M5 findings + ceiling + StateDevice fast-follow (docs merge ae4794f) |
| 3b | `design33/statedevice-auto-classify` | 91c6364 | d80ae39 + aaa41d4 (rig-finding fix) | rig M5: before/after maps both complete@40; six Thorlabs pairs flip declared→auto; iChrome-MLE-TCP.State refused live despite explicit human confirm; auto-classified wheel write passes both gates via `serve` | 9491361 | Gate done: design/33 Block-3b landed note + ELL6/no-core-shutter/iChrome findings (docs merge daab107) |
| 4 | `design32/acquisition-budgets` | 0d0137d, rebased to 1c14271 | 540a641…bce4e32; 894/98/3 (mac), 876/114/3 (M5) | **rig gate PASS 2026-07-26**: G1 1.00× (after list revert; the 3.4× feeder was removed), G3 budgets/confirm before hardware + attributable decline, G5 MDA token sensitive to slice/channel change (bridge reads fixed), G6 ~657 ms/frame overhead. Gate caught 3 defects. | e8e7afb | Gate done: chunking + lazy-feeding both withdrawn on measured evidence; Block 4 landed note + measured G1/G6 + final schema (docs merge `ec98330`) |
| 5 | `design33/dose-authorization` | `ec98330` (main, 894/98/3) | `ff41de7` + `a28e5bd` (review fixes); 910/98/3 (mac) | B0/B1/B5 PASS on M5; B2/B3 PASS off-rig; B4/B4b/B5 PASS on a demo core via the live pyjavaz bridge. Gate found 3 defects, all in the gate not the code; 2 implementation defects were fixed in coordinator review before hardware. | `3438b90` | Gate done: design/33 Phase-3 landed semantics, evidence boundaries, and follow-ups; design/32 planner/ledger discharge + M5 live-geometry measurement |
| 6 | `design32/remote-auth` | `d70874d` (main, 910/98/3) | `eb9ab16` + `c81aa16` (review fixes R1–R4) + `f68af87` (coordinator); 937/98/3 (mac) | n/a — network/security tests are this block's gate; no hardware path touched | `056a4ef` | Gate done: §3 heading restored (it was missing), Block 6 landed contract + five stated limitations, interim "unauthenticated remote warning" path removed from the ordering section |
| 7 | `design32/generated-hook-decisions` | `cc2df05` (main, 937/98/3) | `7a9a602`…`af8f51b`; 967/99/3 (mac), 1044/21/3 (demo core) | demo D1–D5 PASS + M5 R1 PASS 2026-07-28: capability stripping live, 3-of-5 typed stop, four attributable refusals, Run A 12/12/12 with revisit 0.01 px. Gate caught 4 defects, 3 invisible off-rig. | `2b8d752` | Gate done: design/32 §4 Block-7 landed note; design/26 5 reconciliations incl. the unresolved `analyze_frame` collision blocking Block 10 |
| 7b | `design32/hook-illumination-and-artifacts` | `ff690e8` (main, 967/99/3) | `f300fff`…`35f6dc4`; 1014/99/3 (mac), 989/115/3 (M5 under uv), 1089/21/3 (demo core, integration live) | M5 2026-07-28: R1–R8 + P0–P2 + R5pre PASS; D2/D3 skipped by ruling. **Gate caught 4 defects, 2 invisible off-rig** | `e688606` | Gate done: design/32 §4 vocabulary corrected + Block-7b landed note; design/33 illumination-contract changes, failed-write-may-have-landed rule, and M5 405 findings (docs merge `d189722`) |
| 8 | `design29/saved-dataset-foundation` | `c3af2b1` | `b1e347c` + `333144e` (7 review defects); 1052/99/3 | **pre-branch gate: `design29/probe-findings` (`cf0c00c`)** — probe was defective and its affine verdicts void; repaired, MMCore row-major pinned by `javap`. Live M5/demo/M2: **no measured affine anywhere**; identity is MM's default (same hash on two unrelated systems). Saved data gives the X column only (~90°, ~0.13 µm/px). No rig action for the implementation itself. | `e45a129` | Gate done: §5 precedence corrected (explicit ref beats the acquisition record, reconciling §2), measured per-image metadata contract recorded, Y column still open and owned by Block 9 |
| 9 | `design29/stage-coordinate-mosaic` | `fecb93b` | through `82feeb5`; 1092/99/3 | **MERGED.** Gate run + R6. R1/R2/R3 pass — first non-sentinel per-image affine ever recorded. R3b: no objective key even with `Res1` live → acquisition-recorded identity unreachable on M2 (finding, not defect; artifact path mandatory). R4: both affine columns corroborated. R5 answered **offline** — dihedral match proves the renderer does not mirror. R6: affine orientation confirmed to 0.3° but **scales are wrong, −3.5% / −15.7%, anisotropic where the config reports isotropy** — traced by `javap` to Manual-Simple never measuring a scale at all. A calibration-input defect on M2, not a Block 9 code defect. |
| 9b | `design33/read-only-rig-inventory` | | | read-only rig inventory required | | |
| 10 | `design26/completed-dataset-runner` | | | saved-data fixture | | |
| 11 | `design26/generated-adapter-run-b` | | | required | | |
| 12 | `design26/few-shot-run-c` (optional) | | | required | | |
| 13 | `design32/hook-worker-isolation` | | | regression required | | |
| 14 | `design33/extended-authorization` | | | required per phase | | |
| 15 | `design32/context-audit-store` | | | n/a | | |

## Why this order

1. Designs 32 and 33 are safety foundations. Strict parsing comes before live
   completeness checking, and acquisition budgets come before design/33 can authorize
   dose policies.
2. The generated-hook decision boundary lands before new design/26 adapters are asked
   to do more work. The full worker is later because it is a larger isolation project;
   no earlier milestone may claim process containment.
3. Design/29 owns NDTiff traversal, calibration identity, and stage-coordinate mosaic
   geometry. These primitives land before design/26 consumes a mosaic through its
   generic completed-dataset runner.
4. After Block 9 is complete, a small read-only rig-inventory block lands before Block
   10. It turns the Block 7b probe into reusable discovery evidence without generating
   or approving a safety configuration. Block 14 Phase 5 later consumes that stable
   inventory after the typed-actuator and channel-plan schemas have landed.
5. Design/26 Run A has already produced field findings. Re-run it as a regression gate,
   not as if it were unfinished. Run B is the required custom-integration milestone.
   Run C is conditional on a real target needing few-shot learning.
6. Design/32's remote authentication is independent, but the shipped
   `--allow-remote` surface makes it a before-feature-work release gate. Context storage
   is independent and can move earlier on another schedule, but is last here to keep one
   coordinator's path serial and safety-first.

## 0. Baseline and evidence inventory — no implementation branch

- [x] Confirm the worktree state and preserve all pre-existing edits/untracked files.
      Do not fold them into implementation commits. — worktree clean at c90d738.
- [x] Record current `main`, Python/platform versions, and the complete non-hardware test
      baseline. Treat the two socket-bind failures recorded in design/32 as environmental
      only if they reproduce for the same reason. — main c90d738, Py 3.11.15,
      macOS-14.5-arm64; 771 passed / 98 skipped; the two socket-bind failures did NOT
      reproduce (environmental to design/32's machine).
- [x] Inventory every shipped/example/documented safety YAML before strict parsing makes
      ignored keys fatal. — canonical `microclaw/safety_config.example.yaml`, fixture
      `tests/fixtures/safety_config.yaml`, stale `build/lib/...` copy (non-authoritative),
      README doc surface. All keys in shipped files are already recognized, so strict
      rejection breaks neither; migration risk is field configs only.
- [x] Locate the 2026-07-20 design/26 Run A history, hook log, selected-position list,
      survey, revisit dataset, and manifest. Verify hashes where available. — located in
      OneDrive `microclaw-json-histories/` (`..._run_a.json` + `run-a/`); hash verify
      deferred to Block 7 when it becomes the regression baseline.
- [-] Locate representative saved NDTiff fixtures: multi-position grid, non-grid spiral,
      non-square images if available, and the 2500-tile dataset. — PARTIAL: Run A
      `run_a_1` is a grid with non-square 453×227 frames. The design/30 spiral and
      2500-tile datasets are not here; retrieve them from the rig before Block 9.
- [-] Run the no-hardware `design/26-roi-detection-spike.py` baseline and retain output.
      Do not use its synthetic accuracy as field acceptance. — DEFERRED to Block 7/12
      (not a Block 1 gate).
- [x] Decide where rig evidence is stored. Keep each run's commands, stdout/stderr,
      environment identity, artifacts, hashes, and verdict in one dated directory. — one
      dated dir per rig run; Block 1 requires no rig evidence.
- [x] Stop if any design is changing concurrently. Rebase the ordering on the merged
      design text before creating Block 1. — no concurrent design edits; worktree clean.

## 1. Design/32 Finding 1a — schema-version-free hardening

Branch: `design32/config-hardening`

- [x] Create the branch from updated `main`.
- [x] Reject non-mapping YAML roots and unknown top-level and section keys with
      file-anchored, aggregated errors.
- [x] Reject booleans, non-numbers, NaN, and infinities in configured numeric fields.
- [x] Enforce existing-field semantics: ordered finite bounds and positive
      `max_exposure_ms`.
- [x] Add one shared fail-closed finite-number validator to every public numeric guard,
      including hardware values read during a check.
- [x] Add migration diagnostics for every shipped/documented config found in Block 0;
      do not introduce `schema_version` or the new range representation yet.
- [x] Run config, guard, tool, entry-point, and full non-hardware tests plus static checks.
      — coordinator-verified on branch: 803 passed / 98 skipped / 3 baseline warnings;
      compileall + `git diff --check` clean.
- [x] Commit and hand back to the coordinator; coordinator reviews and merges. — impl
      967ab5d, merged --no-ff as 56ed945.

Post-merge design gate:

- [x] On updated `main`, compare behavior with design/32 Finding 1. If names, accepted
      legacy shapes, or migration impact differ, update design/32 on a docs branch and
      merge it before Block 2. Otherwise record “no update required” in the ledger.
      — config parsing matches Finding 1; recorded the measured zero shipped-file
      migration impact AND the non-numeric-write fail-closed behavior change in design/32
      (docs branch merged c86af6d).

## 2. Design/32 Finding 1b — versioned strict safety schema

Branch: `design32/versioned-safety-schema`

- [x] Create the branch from updated `main` after Block 1's design gate.
- [x] Add mandatory `schema_version` and a documented migration path.
- [x] Implement `RangeEdge`, `RangePolicy`, structured `ActuatorId`, and
      `ParsedSafetyConfig`; derive runtime constraints from the authoritative policies
      once rather than parsing two representations. — `_stage_constraints` derives core
      `StageConstraints` + `named_stages` in one pass over the single `ranges` map.
- [x] Require complete fields for every declared constraint and an explicit policy for
      both edges of each declared stage/focus range.
- [x] Preserve reviewed-unbounded reasons for audit/degraded use; do not describe them as
      guaranteed containment.
- [x] Make loaded `ParsedSafetyConfig` the canonical entry-point contract. Ensure directly
      constructed test dataclasses cannot masquerade as reviewed startup configuration.
- [x] Migrate examples and docs; test old-version errors, typo reporting, aggregate
      failures, range edges, and CLI/web startup parity.
- [x] Commit, review, and merge. — impl d5aa5c4; coordinator caught+fixed a blocker
      (analysis.min_snr wrongly required, broke shipped example) as 12b0677 + regression
      test; coordinator-verified 816 passed / 98 skipped; rig starts clean; merged b8092c4.

Post-merge design gate:

- [x] Update design/32's stubs and migration facts if implementation differs. — 1b landed
      note added (docs merge 8dd521f): SafetyConfigError(ValueError), from_yaml on
      ParsedSafetyConfig, single-pass named-stage derivation, required-when-present policy.
- [x] Update design/33 if the final `ParsedSafetyConfig`/`ActuatorId` API or open-edge
      representation differs from what its live cross-check assumes. Merge doc changes
      before Block 3. — no change needed: landed API matches design/33 §"Reviewed-unbounded
      range edges must survive" exactly (core_xy/core_focus/named identities, bound-None
      open edge, ranges keyed by ActuatorId).

## 3. Design/33 Phase 1 — core authorization map

Branch: `design33/core-authorization-map`

- [x] Create the branch from updated `main`.
- [x] Add the declared rig-profile schema and minimum classification registry. — schema
      checkpoint approved after revision (no actuator manifest; built-ins are code-level).
- [x] Require property allowlist mode in guaranteed mode, or disable the generic setter.
      — guaranteed requires `categorical_properties` (incl. empty); else fails closed.
- [x] Mechanically enumerate the effective write surface: dedicated tools, generic
      properties, channel effects, autofocus, acquisitions, and plugins.
- [x] Implement shared `validate_live_rig(ctrl, parsed_config)` and call it in CLI and web
      startup after connection but before prompts, app construction, agent invocation, or
      tool dispatch.
- [x] Bind tagged core XY/focus identities to live devices; reject conflicts with named
      declarations, undeclared reachable actuators, open reachable stage edges, unknown
      continuous semantics, and opaque motion plugins in guaranteed mode.
- [x] Exclude unclassified preset/property effects in this phase. Do not approximate a
      channel executor yet. — illumination added as a Phase-1 built-in capability (review fix).
- [x] Test every path to the same actuator, not just each high-level tool in isolation.
- [x] Commit but **do not merge until the following rig spike passes**. — impl fc346a4.

Rig gate:

- [x] On a rig-safe checkout, first prove a representative incomplete profile fails before
      any mutation tool becomes reachable in both CLI and web modes. — M5: deleting
      PIZStage range refused identically across CLI/serve/authorization-map.
- [x] With the reviewed rig profile, enumerate the effective map and compare it manually
      with connected devices and enabled write paths. — M5 map complete, cross-checked
      against the MM .cfg; excluded list = FPGA/COM/serial + operator-undeclared devices.
- [x] Prove allowed read-only startup works and representative guarded writes still pass.
      — categorical (filter wheel) + illumination (iBeam power + confirm-gated enable)
      writes passed; excluded (PWM.Position0) and name-mismatched writes refused.
- [x] Stop on any undeclared reachable device, path-classification ambiguity, CLI/web
      mismatch, or write occurring before validation. Fix on the branch and repeat. — none;
      two blockers (illumination, preset UX) caught in review and fixed pre-merge.
- [x] Coordinator reviews evidence and merges only after the fail-closed test is observed.
      — merged 0124ffd.

Post-merge design gate:

- [x] Update design/33 with the actual rig profile, classifications, unsupported/excluded
      paths, and field findings. Update design/32 wherever it claims coverage now proven
      or disproven. Merge documentation before Block 4. — design/33 Phase-1 landed note +
      M5 findings + FPGA-laser ceiling + StateDevice fast-follow + illum-units/reload
      caveats (docs merge ae4794f). No design/32 change required (its Finding 1 coverage
      claims are consistent; the reload/units notes live in the design/33 landed note).

## 3b. Design/33 Phase 1 refinement — StateDevice auto-classification

Branch: `design33/statedevice-auto-classify`

Agreed with the operator after Block 3 (option 1 shipped as-is + this as option 2).
Rationale: guaranteed-mode allowlist declaration is disproportionate for benign discrete
devices; M5 authoring (two Thorlabs filter wheels + ELL6) exposed the friction. See
[[project_design33_statedevice_fastfollow]] and design/33's "Phase 1 fast-follow landed"
note.

- [x] Create the branch from updated `main`. — from `91c6364`.
- [x] Auto-classify any device `core.get_device_type()` reports as a **StateDevice**
      (filter wheels, sliders, turrets) as `reviewed_categorical_property` for its
      State/Label writes, WITHOUT requiring a `categorical_properties` declaration.
- [x] **Shutter carve-out:** a shutter-type device (Core.Shutter, MM `ShutterDevice`,
      or a state device that gates light) must NOT be auto-classified — it stays subject
      to the illumination gate. Pin the mechanical shutter test; do not infer from names.
      — decided only by device type / `Core.Shutter` / the `illumination:` block; pinned by
      `test_core_shutter_is_never_auto_classified_even_when_typed_state_device`.
- [x] Keep explicit declarations working (auto-classification is additive, not a replacement).
      Do not auto-admit continuous or generic devices — StateDevice type only.
      — **strengthened after the rig gate:** auto-classification fills vacuums ONLY. A
      ruling on either position property (categorical, excluded, or forbidden) takes that
      device's whole discrete position off the table. See the M5 finding below.
- [x] Migrate example/README: filter wheels/sliders/turrets no longer need declaration;
      shutters still do.
- [x] Test: a StateDevice auto-classifies categorical; a ShutterDevice does NOT; explicit
      declarations still work; a non-state device is unaffected; the runtime allowlist
      admits an auto-classified write and still refuses an excluded one. — plus the named
      M5 regression `test_declaring_one_position_property_does_not_auto_admit_the_other_m5`.
- [x] Commit; do not merge until the rig check passes. — impl `d80ae39`, rig-finding fix
      `aaa41d4`; `main` 863 passed / 98 skipped after merge.

Rig gate (small):

- [x] On M5 (or demo), remove the filter-wheel/ELL6 `categorical_properties` entries, run
      `authorization-map`, and confirm they are now auto-classified categorical (not
      excluded) and the map is still `complete`. Confirm a shutter/illumination device is
      NOT silently auto-admitted. — both maps `complete` at 40 entries; the six Thorlabs
      pairs flip `declared`→`auto:state-device` and nothing else changes; illumination and
      the 20-device excluded inventory identical.
- [x] Coordinator reviews evidence and merges after the rig check. — merged `9491361`.
      **The gate caught a real defect.** The first implementation auto-admitted
      `iChrome-MLE-TCP.State` — on a laser engine, against an operator who had declared
      only `Label` — and did so in the *unmodified* config, i.e. it would have widened M5
      without any config edit. Fixed on-branch by the vacuum-filling rule, then re-verified
      live: the write is refused even after an explicit human confirmation in the session.
      An auto-classified filter-wheel write passes both gates through `serve`.

Post-merge design gate:

- [x] Update design/33's "Phase 1 landed" note with the final StateDevice rule, the exact
      shutter carve-out test, and the M5 result. Merge docs before Block 4. — design/33
      "Phase 1 fast-follow landed" section (docs merge `daab107`), including three findings
      the gate produced: ELL6 is a Bertrand-lens flip and does not gate light; **M5 has no
      core shutter**, so the carve-out rests entirely on the `illumination:` block being
      complete; and `iChrome-MLE-TCP.Label` remains a bare categorical write on a laser
      engine in the reviewed M5 profile (open item on the rig config, not on this code).
      A fourth finding — that `iChrome-MLE-TCP.State` might gate emission — was recorded
      and then **retracted**: the operator reproduced the writes manually and the laser
      did not turn on (an EMU GUI power click had enabled it). Correction merged in the
      design note; the vacuum-filling rule does not rest on it.

## 4. Design/32 Finding 2 — acquisition plans, budgets, ledger, and batching

Branch: `design32/acquisition-budgets`

- [x] Create the branch from updated `main`. — `design32/acquisition-budgets` from `0d0137d`,
      rebased onto `1c14271` after the chunking-premise reconciliation.
- [x] Define a common `AcquisitionPlan` for frames, per-frame exposure, estimated
      duration, bytes, and illuminated time. — `microclaw/acquisition.py`.
- [x] Add strict config fields for hard budgets and lower confirmation thresholds.
      — nine-field `acquisition:` section, **required** (coordinator review fix; optional
      made the P0 fail open, reintroducing Finding 1's own thesis).
- [x] Plan before motion; reuse the existing confirmation mechanism with an acquisition
      kind; reserve conservatively and commit actual usage as frames complete.
      — `_authorize_acquisition` reuses `CONFIRM_FN(..., kind="acquisition")`.
- [x] Apply the planner to timelapse, Z stack, tiles/multiposition, adaptive survey, and
      MMStudio MDA. Reject unplannable/unbounded work. — all seven entry points routed;
      MDA is planned from `_read_mda_settings` and credits the ledger after its opaque call.
- [!] ~~Make cancellation possible by feeding events lazily inside one `Acquisition`~~
      — **WITHDRAWN 2026-07-24 on measured rig evidence.** Lazy generator feeding costs
      **3.14×** on M5 (+74 ms/frame) and no look-ahead depth fixes it: the engine pulls
      one event at a time from Python regardless. Cancellation is also unreachable today
      (nothing calls `Acquisition.abort()`). List-known acquisitions pass the **list**;
      `image_saved_fn` accounting is free (1.00×) and stays. Cancellation is deferred to
      its own block, which must ship the abort trigger with the mechanism. See design/32
      §2 "Second reconciliation (2026-07-24)".
      — SUPERSEDED pre-withdrawal note, retained for the record: the one-event-in-flight
      gate was believed load-bearing and its cost unmeasured. pycro-manager 1.0.2's
      `EventQueue.get()` eagerly advances a queued generator without waiting for an image
      (verified from installed source + a fake-acquisition test), so an ungated generator is
      drained and gives no cancellation granularity at all. G1 then measured the gated
      feeder at 3.14× and the mechanism was removed; nothing in the shipped code depends on
      this paragraph.
- [x] Test limits, confirmations, cancellation, reservation rollback, partial completion,
      cumulative ledger behavior, and all acquisition entry points. — 892 passed / 98
      skipped / 3 warnings (+29 over the 863 `main` baseline).
- [x] Commit but do not merge before the rig gate. — impl `a8f0209` + `8d7e832`
      (three coordinator-review blockers), coordinator fix `01b2766`; pushed to origin.

Rig gate:

- [x] Run no-exposure/dark or safest representative plans around warning and hard limits.
      — G3 exercised budgets and the confirmation threshold before any hardware call and
      proved a decline is attributable; G6 ran a 9-position dark grid (50 µm, 100 ms).
- [x] **Measure the feeder's throughput cost.** — DONE, and it rejected the design:
      3.40× at 5 ms and 3.94× at 50 ms through microclaw; isolated to generator-vs-list
      at 3.14× by the raw-bridge 2×2 probe. Accounting via `image_saved_fn` measured free
      (1.00×). Evidence: `design/32-block4-bridge-cost-probe.py`,
      `design/32-block4-g1-diagnose.py`.
- [x] Re-run G1 after the list revert. — **PASS 2026-07-26**: 1.002× @5ms (7.015 vs
      7.000 s), 0.997× @50ms (5.484 vs 5.500 s). Regression fully closed; `image_saved_fn`
      accounting stays free in the full path.
- [x] Cancellation latency is **not** measured in this block — the capability is deferred.
      — recorded as a deferral, not a test (G2 rewritten as a deferral record, `2a95607`).
      Cancellation ships with its own abort trigger in a later block.
- [x] Verify the MDA preview token round-trips: `settings.slices()` / `settings.channels()`
      with `spec.useChannel` / `spec.exposure` is unverified bridge code, and a wrong field
      name breaks the `get_mda_settings` → `run_mda` pairing at the point of use.
      — G5 PASS: the guessed reads were wrong and the probe caught it (`f7b1602`,
      `2f94c87`); fixed in `138a70d` (bridge `size()`/`get(i)`; `exposure()` is a method,
      `useChannel` a field) and the stale-token refusal pinned in `bce4e32`. The token is
      sensitive to a slice/channel change.
- [-] Verify planned/acquired counts, duration, bytes estimate, illuminated-time ledger,
      partial failure, and restart/session semantics. — counts/bytes/illuminated ledger and
      the duration estimate verified on the rig (G1, G3, G6: actual ≈ 7.6× the
      exposure-only estimate, so `max_duration_s` bounds the estimate, not wall time).
      **DEFERRED, unit-pinned only:** partial failure and restart/session semantics — the
      ledger is per-session and not durable. Block 5 owns the cumulative-session policy.
- [x] Stop if actual execution can exceed a reservation silently or if cancellation
      granularity is materially worse than documented. Fix and repeat. — the gate caught
      three defects (G1 feeder cost, G3 unattributable decline, G5 wrong bridge reads); all
      fixed on-branch and re-measured before merge.
- [x] Merge after review. — merged `e8e7afb`.

Post-merge design gate:

- [x] Replace design/32 estimates with measured batch/cancellation findings and final
      schema names. If the rig result changes acceptable budgets or architecture, update
      the design before Block 5. — design/32 §2 "Landed: Block 4" note: final planner/
      ledger/config shape, measured G1 (1.00×) and G6 (~657 ms/frame, 7.6×), the
      lists-not-generators architecture, and the deferred items. No architecture change
      beyond what the two reconciliations already recorded.

## 5. Design/33 Phase 3 — acquisition/dose authorization extension

Branch: `design33/dose-authorization`

- [x] Create the branch from updated `main`. — from `ec98330`.
- [x] Add Block 4's frame, duration, byte, illuminated-time, and cumulative-session
      policies to the authorization map and completeness report. — nine
      `acquisition-policy:*` rows under a new `acquisition-dose` built-in typed
      capability. The session row states in the report that the ledger is an in-memory
      controller session that resets on process restart; Block 5 does NOT make it durable.
- [x] Ensure every acquisition path is classified and checked; per-frame exposure alone
      must never count as complete acquisition authorization. — the old
      `path="acquisition", capability="exposure"` row is deleted (pinned by an explicit
      negative assertion). Eight `acquisition-tool:*` rows, `run_mda` deliberately NOT
      among them; `execute_tool` checks the row before dispatch.
- [x] Test missing/partial dose declarations, aliases, all planners, and CLI/web startup.
      — each of the nine fields parametrized as the missing one; guaranteed mode fails
      closed before any prompt or app construction in both CLI and web. "Aliases" resolved
      to the adaptive wrappers `run_adaptive_zstack`/`run_adaptive_timelapse`, which reach
      the planner transitively and are covered by the AST tripwire.
- [x] Run a short rig regression proving incomplete dose policy fails before tools and a
      complete policy produces the same authorized plan/ledger as Block 4. — **PASS
      2026-07-27**; procedure and verdicts in `design/33-block5-rig-gate-prompts.md`
      (B0–B5). B0/B1/B5 on M5, B2/B3 off-rig, B4/B4b/B5 on a demo core. The gate found
      three defects, **all in the gate itself, none in Block 5's code**.
- [x] Commit, review, and merge. — impl `ff41de7`, coordinator-review fixes `a28e5bd`
      (two blockers, both coordinator-verified); 910 passed / 98 skipped / 3 warnings.

Post-merge design gate:

- [x] Update design/33 Phase 3 and design/32 Finding 2 with the final shared policy names,
      completeness semantics, and rig evidence before Block 6. — design/33 now records
      the nine policy/eight tool rows, deleted exposure-only claim, lockstep degraded
      semantics, derived tool coverage, MDA exclusion, evidence boundaries, and
      follow-ups; design/32 records how Block 5 consumed its planner/ledger plus M5's
      live-geometry accounting measurement.

## 6. Design/32 Finding 3 — remote authentication release gate

Branch: `design32/remote-auth`

- [x] Create the branch from updated `main`. — from `d70874d`.
- [x] Require a high-entropy token for non-loopback mode and constant-time comparison.
      — `MICROCLAW_REMOTE_TOKEN` (min 32 chars) or `secrets.token_urlsafe(32)`;
      `hmac.compare_digest` for token, pairing codes, and session values.
      **Length-checked, not entropy-checked** — recorded as a limitation, not a defect.
- [x] Protect every remote `/api/*` control route, including stop and confirmation.
      — widened beyond the three control routes: history, model, key, and artifact are
      authenticated too. `POST /api/pair` is the only exception; `/` and `/favicon.ico`
      stay public so the page can load in order to pair.
- [x] Add one-time, expiring, rate-limited browser pairing that yields an HttpOnly,
      Secure, SameSite=Strict session cookie; never expose the long-lived bearer token to
      browser JavaScript. — code delivered in the URL *fragment* so it reaches neither the
      request line nor the proxy access log; single-use, 15-min TTL, five outstanding max.
      `POST /api/pair/code` is bearer-only: a cookie cannot mint codes.
- [x] Refuse untrusted cleartext non-loopback operation unless explicitly behind the
      supported trusted TLS-proxy mode. — new `--behind-tls-proxy`; `--allow-remote` alone
      now exits. **Behaviour change for anyone running remote mode on a trusted LAN.**
- [x] Preserve Origin/CSRF checks and existing non-loopback credential-editing gates.
      — both kept, independently tested; the CSRF expected origin becomes `https://` +
      `Host` in proxy mode (verified against a non-default public port).
- [x] Add request limits, rate limits, identity-bearing confirmation audit records, and
      tests for clients with missing/forged Origin, `Bearer None`, replayed pairing codes,
      and every protected route. — 256 KiB prompt / 64 KiB JSON → 413; 10 failures and 10
      pairing attempts per 60 s per socket address over bounded LRU maps; audit is
      volatile by design (durable audit is Block 15).
- [x] Commit, review, and merge. No rig action is required; network/security tests are.
      — merged `056a4ef`. Coordinator review found one blocker (remote startup printed
      `https://` URLs for the *cleartext* bind port and auto-opened a browser at an
      address that cannot answer) plus three smaller items; all fixed in `c81aa16`.
      The coordinator also independently probed the two risks the tests did not cover:
      a real streaming remote turn through the body-consuming middleware, and the
      proxy-mode Origin rewrite. Both pass on starlette 1.3.1.

Post-merge design gate:

- [x] Update design/32 with the actual pairing/TLS contract and explicitly remove any
      interim “unauthenticated remote” warning path that is no longer true. — "Landed:
      Block 6" note added with the shipped contract and five stated limitations
      (unverifiable proxy assertion, token length ≠ entropy, no revocation, shared-address
      rate limiting behind a proxy, volatile audit). The ordering section's interim
      "startup warning instead of a token gate" path is struck. Also repaired: Finding 3
      had **no `## 3.` heading** and had been running on from Finding 2.

## 7. Design/32 Finding 4 Phase 1 — capability-limited generated-hook decisions

Branch: `design32/generated-hook-decisions`

- [x] Create the branch from updated `main`. — from `cc2df05`.
- [x] Separate trusted built-in control hooks from generated/user analysis hooks.
- [x] Define a closed, discriminated result/action schema covering measurements,
      `MoveStage`, `AcquireAt`, `SetExposure`, `ContinueSurvey`, `StopSurvey`, and
      `RequestAutofocus`.
- [x] Stop supplying generated analysis hooks with controller, guard, credentials, or a
      hardware event queue. — plus `candidates`/`progress`/`survey_events`/`log_path`;
      `hook_params` cannot smuggle them back.
- [x] Validate and convert every proposal in trusted parent code through safety, dose,
      confirmation, cancellation, and audit gates. Reject unknown/unsupported actions.
- [x] Preserve stateful adaptive behavior without claiming worker isolation, hard
      deadlines, memory caps, network isolation, or native-crash recovery.
- [x] Run all hook and adaptive-survey tests. — 967/99/3 mac, 1044/21/3 demo core.
- [x] Re-run design/26 Run A on the rig as a regression gate using
      `design/26-field-spike-prompts.md` A1–A3. Compare record counts, image retention,
      deterministic ranking, guard validation, and revisit accuracy with the retained
      2026-07-20 evidence. Do not claim biological or object-level validation.
- [x] Stop and fix if Run A changes acquisition, drops images/records, bypasses a parent
      gate, or cannot replay exactly. Then commit, review, and merge. — merged `2b8d752`.
- [x] **Before merging, retain M5's saved-hook source and manifest.** Copy
      `~/.microclaw/hooks/*.py` and `manifest.json` off the rig and hash them. Block 7
      refuses all three of that registry's hooks, and their source is the only input
      Block 7b's migration has. Nothing in the repo holds a copy.
      **Copy, do not delete.** The saved hooks do not affect any gate — nothing
      resolves a hook it was not asked for — and Block 7 already refuses them loudly
      and before any hardware moves. Deleting a `.py` while its manifest entry remains
      turns that clean refusal into a `FileNotFoundError`, and Block 7b wants them in
      the live registry to verify the migration. Hash at both ends: a copy that
      translates line endings is not faithful. Those file hashes will not equal the
      `sha256` in `manifest.json`, which pins LF-normalized text rather than the bytes
      on disk (D2 finding); that is expected, not corruption.

Post-merge design gate:

- [x] Update design/32 with the final trusted/generated category and action schemas.
      — §4 "Landed: Block 7" note.
- [x] Update design/26 only if its hook contract, Run A instructions, or current-runtime
      containment caveat changed. Merge doc corrections before Block 8. — five changed:
      saved adapters must not inherit `HookBase`; `image_saved_fn` is wired (Block 4);
      the shared observation writer now exists; the capability boundary is no longer
      offline-only; and **`analyze_frame` now means two different things** — flagged
      UNRESOLVED, to be settled before Block 10 branches.

## 7b. Design/32 Finding 4 Phase 1 fast-follow — restore what the union cannot express

Branch: `design32/hook-illumination-and-artifacts`

Block 7's closed action union covers adaptive *acquisition* decisions and nothing
else. Three capabilities the lab actually uses fall outside it. This block owns
getting them back. **Block 13 does not.** Phase 2 is process isolation for the same
contract — its bullets keep controller and guard out of the worker and add no
action types — so without this block none of them returns.

**1. Illumination control, for UV activation.** Confirmed as a live requirement
(2026-07-28), not a historical artifact of one deleted hook. Ramping 405 nm
activation against measured blink density is the canonical SMLM feedback loop and
is exactly the "analysis influences acquisition" case design/32 §4 says the
boundary must not forbid. It is also the hardest to authorize: the loop is
**per-frame and closed**, so `require_confirm_on_enable` cannot be satisfied by
prompting — see the confirmation bullet below. Getting this wrong in either
direction is serious: too permissive and generated code drives a UV laser
unsupervised; too strict and the microscope cannot do the experiment.

**2. Live artifact emission.** `mosaic_stitcher` and `mosaic_stitcher_rot` write an
assembled 16-bit TIFF; Phase 1 gave hooks no artifact path at all.

**3. Frame discard.** `filament_position_filter` returns `None` to drop an
uninteresting field. `analyze_frame` **cannot express this** — the adapter always
returns `(image, metadata)`. Either add a discard action or keep a supported
observation-only callback shape. Note what discard does and does not buy: per
design/27 the position is still moved to and still exposed, so this saves storage,
not dose. Do not describe it as skipping acquisition.

Fixtures, read from the retained source (2026-07-28), not inferred:

| Hook | Blocked by | Needs |
|---|---|---|
| `filament_position_filter` | `HookBase` + `log_path` | measurements; **frame discard** |
| `mosaic_cell_counter` | `HookBase` + `log_path` | measurements + cross-frame state (state already survives) |
| `mosaic_stitcher` | `HookBase` + `log_path` | **artifact write** |
| `mosaic_stitcher_rot` | `HookBase` + `log_path` | **artifact write** |

All four subclass `HookBase` and take `log_path`, so all four are refused at
resolve time today. **None takes `ctrl` or `guard`, and none touches
`event_queue`** — so no current hook needs the capability Phase 1 was written to
remove, and migration is mostly mechanical once the three gaps above are closed.

Note what the stitchers do today: `tifffile.imwrite(self.out_path, canvas)` with
`out_path` an unconfined constructor string, plus an `np.save(out_path + ".npy")`
fallback. They can write anywhere on disk. The replacement must be confined to the
acquisition's artifact directory — this block should end with generated hooks
holding *less* filesystem reach than before, not more.

- [x] Create the branch from updated `main`. — from `ff690e8`.
- [x] Add a typed illumination action to the closed union, authorized through
      design/33's **existing** Phase-1 illumination gate. — `SetIlluminationPower`;
      every proposal routes through `check_illumination`, which gained a
      `previous_percent` parameter so the ratchet uses the parent's own last write
      instead of a per-frame device read. No second authorization surface.
- [x] **Resolve the confirmation problem before coding.** — resolved by construction:
      a hook cannot enable a shutter (the union has no way to express it), so
      `require_confirm_on_enable` is never reached mid-run and no prompt can land on a
      callback thread. Hook illumination is power modulation only, inside an envelope
      confirmed once before any motion, alongside the Block 4 reservation.
- [x] Add a bounded, parent-mediated artifact-emission path. — `EmitArtifact` carries
      bytes/ndarray plus a bare filename; the parent writes, size- and count-limits,
      hashes, and records. **The rig gate caught this landing in the wrong directory**:
      the path was predicted from `save_dir/name` and missed AcqEngJ's `_N` rename, so
      artifacts filed beside their dataset and collided across runs. Fixed by
      late-binding from `_acq_dataset_path` inside `_acquire_with_hooks` (`30f2450`).
- [x] Add a frame-discard path. — `DiscardFrame`; docs state in all three places that
      the position is still moved to and still exposed (design/27).
- [x] Distinguish open-loop conditioning from closed-loop feedback. — documented in
      `hook_docs.py`; no open-loop built-in was added, deliberately.
- [x] Migrate all ~~three~~ **four** hooks to `analyze_frame`. — the fixture table
      always listed four; the prose said three. All four migrated, committed under
      `tests/fixtures/hooks/m5_migrated/`, with the originals retained byte-for-byte
      under `m5_legacy/` (marked `binary` in `.gitattributes`, hashes verified through
      a real Windows checkout).
- [x] Test refusal paths as carefully as success. — ceiling, step factor, spent
      budget, no envelope, oversize artifact, per-run total, count limit, filename
      escape and collision, and `hook_params` smuggling.

Rig gate:

- [x] Re-run each migrated hook on M5. — all four run (R1). Fidelity checked off-rig
      instead of against "retained pre-Block-7 evidence", which did not exist — Block 7
      retained the source, not its outputs. `design/32-block7b-r2-fidelity-diff.py`
      feeds one dataset's frames to both versions in the same process: **zero
      mismatches across all four** (R2). No fourth gap was found.
- [-] Exercise UV activation on a real closed loop. — **DEFERRED, and not met.** M5's
      405 nm line is `iChrome-MLE-TCP.Laser 4: 3. Level %`, real and linearized, but it
      was undeclared until this gate and htSMLM ramps FPGA *pulse duration*
      (`Laser Trigger.Duration0`) rather than level — so dose is level × duration and
      only the first is bounded. The envelope path is proven completely (R5/R5b/R5c:
      20 accepted writes, 64 ceiling refusals, 12 budget refusals, a step-factor
      refusal, and a wind-down accepted with the budget spent), but the fixture is a
      deterministic ramp, not feedback, and every run was with the laser disarmed.
      Pulse duration as a bounded actuator is design/33 Phase 2 by operator ruling.

Post-merge design gate:

- [x] Update design/32 §4's action vocabulary; update design/33 if the illumination
      gate's contract changed. — docs merge `d189722`. design/32 §4 now says *why*
      the six-variant minimum was wrong (derived from the runner's needs, not from what
      the rig's hooks did) and carries a "Landed: Block 7b" note with the nine-variant
      union, the envelope contract, the four gate-found defects, and three limitations.
      design/33 gets what binds the *gate* rather than one block: `previous_percent`,
      why hooks can modulate but never enable, the ratchet's inertness from zero, the
      measured units caveat (M5's cap could never refuse a write), M5's 405 path, and
      that **a failed write may have landed** — which constrains any future typed
      actuator ratcheting against a remembered value. All four hooks were restored;
      none became built-ins; none remain unsupported.

**Scheduling.** M5's three hooks stay refused from the moment Block 7 merges until
this block lands. Run it before Block 8 if the lab needs them; deferring is a
legitimate choice, but the loss is live in the meantime, not theoretical.

## 8. Design/29 foundation — traversal and immutable calibration identity

Branch: `design29/saved-dataset-foundation`

Pre-branch spike/read-only probe (branch `design29/probe-findings`):

- [x] **Stopped before writing calibration code.** Original M5/demo/M2 affine
      verdicts are unusable because the decoder was defective.
- [x] Repair all six defects and pin MMCore row-major ordering with off-rig tests;
      `javap` confirms `AffineUtils` indices `0,3,1,4,2,5`.
- [x] Run fixed `--dataset` on all three Run A datasets and retain output outside
      the repo. Per-image affine is all-zero and summary affine is `Undefined`.
- [x] Retain qualified system/config/dataset findings and verdicts in design/29.
- [x] Complete the convention check on the Run A datasets. — done as far as saved
      data allows: `run_a_1` is dark and rejected, the sparse revisit has no adjacent
      pair, and `run_a_2` determines the **X** column (7/9 pairs, ~90° row displacement
      at 0.1227–0.1439 µm/px) but not Y. **The unresolved Y column moved to Block 9's
      prerequisites**, where it actually binds: nothing Block 8 shipped consumes an
      affine's values, while Block 9 cannot place a tile without a full 2×2.
- [x] Stop if the affine is missing/singular, configuration selection is ambiguous, the
      dataset does not identify the historical transform, or Java/Python row-column signs
      are unresolved. Saved data resolves only X; keep the implementation gate
      closed. Update design/29 with the measured result and revise this block's
      resolver assumptions before creating the implementation branch.
- [x] Run the fixed live probe on M5, demo, and M2, retaining raw and by-ID
      affines and every expected/live rule comparison. M5 is genuinely
      unconfigured (no configs; current all-zero). M2's `Res0` is blocked exactly
      by `SmarActXY.Frequency` (`5` expected, `5000` live), but its own affine is
      identity. Demo's active `Res10x` and inactive `Res20x`/`Res40x` all share
      the same identity and hash, also shared by M2: positive evidence of MM's
      default identity, not calibration. Across these three systems MM exposes
      no measured affine.
      Gate questions 1–3 are answered. The only remaining implementation-branch
      blocker is the unresolved Y column of the 2×2 convention; X is measured as
      ~90° stage/camera rotation at ~0.13 µm/px and remains a consistency check.

Implementation:

- [x] Create the branch from updated `main` only after the probe/design gate passes.
      — `design29/saved-dataset-foundation` from `c3af2b1`.
      **Coordinator ruling (2026-07-28): opened with the convention bullet still
      unticked.** The gate exists to stop a resolver being built on wrong
      assumptions, and those assumptions are now measured and revised (row-major
      ordering, the three fall-through sentinels, camera identity, never inferring
      uncalibrated from a zero scalar). Walking the seven implementation items,
      none consumes an affine's *values*: traversal, canonical serialization,
      hashing, version keys, and precedence policy all work on any affine, and the
      tests run on synthetic ones. A measured 2×2 is a **Block 9** requirement
      (placement), not a Block 8 one.
      The one accepted risk: if MM's calibrator writes its affine somewhere other
      than where `get_pixel_size_affine_by_id` reads, the resolver's MM-sourced
      branch needs rework. That is one branch of one function, and one calibrator
      run plus one probe rerun falsifies it. Re-check before merging Block 8.
      **Discharged at merge, and the risk was overstated.** The resolver's
      acquisition branch reads per-image `PixelSizeAffine` from the saved dataset,
      not from the live core, and MM stamps that field from whatever the active
      config holds at acquisition time. So where the calibrator writes does not
      change this code; it only determines whether *future* datasets carry a real
      affine. Live config reads survive only in the diagnostic
      `_config_mismatches`. The genuine residual limitation is different and is
      recorded below: no dataset available to us carries a non-sentinel affine.
- [x] Extract `_iter_present_coords(dataset, fixed_axes)` from the exporter; iterate real
      axis values and guard candidates with `has_image`. Refactor the exporter to use it.
      — extracted; handles string position coords, per-dataset axis sets, and sparse
      hypercubes. Exporter keeps its zero-fill (its concern, not the helper's).
- [x] Define canonical affine serialization/hash rules and immutable version keys; retain
      the objective/binning alias only as a mutable current pointer.
      — compact sorted JSON, `allow_nan=False`, SHA-256; version key embeds the hash and
      the alias is demoted to a `current_version` pointer.
- [x] Implement the revised tagged resolver. Parse acquisition `PixelSizeAffine`
      in MMCore row-major order and fall through on `Undefined`, all-zero,
      identity, non-finite, or singular values. Never infer uncalibrated from
      current pixel size/config alone: enumerate inactive calibrated configs and
      surface rule mismatches. Never silently use current microscope calibration
      for historical data. Identity includes camera/model, objective, binning,
      ROI geometry, exact affine payload, and hash.
- [x] Validate immutable payload hashes on load and migrate/version legacy aliases before
      use. — hash re-derived and checked against both the stored field and the key suffix.
- [x] Test sparse/non-zero-based coordinates, selection errors, exporter regression,
      recalibration immutability, artifact/knowledge/current/legacy sources, and replay
      without the knowledge base. — replay verified after deleting the knowledge file.
- [x] Commit and merge only once the implementation records uncertainty honestly and requires an
      explicit artifact/confirmation where the dataset is insufficient. — satisfied: an
      incomplete acquisition identity falls through and records
      `acquisition_fallthrough_reason` rather than guessing, and a missing objective is
      never stringified into the hashed payload.

Post-merge design gate:

- [x] Update design/29 with the probe findings, actual MM metadata/affine sources,
      convention result, and final identity format. If the premise of full affine
      placement changed, revise design/29 and re-plan Block 9 before coding it.
      — §5's precedence list **corrected**: it put the acquisition record first,
      contradicting §2's "if omitted"; the explicit reference now wins, which is
      what shipped. The measured per-image metadata contract is recorded as a
      table (dash-delimited ROI, `Core-Camera`, `'1'` vs `'1x1'` binning, and the
      **absent** objective key), because Block 8's first implementation was green
      against invented key shapes and wrong against every real one.
      The premise of full affine placement did **not** change, so Block 9 needs no
      re-plan — but it inherits two open items: the **Y column** (operator action,
      MM's own pixel calibrator) and the fact that **no available dataset carries a
      non-sentinel affine**, so the acquisition branch's success path is
      unit-tested and never field-verified.

## 9. Design/29 feature — pure geometry and stage-coordinate mosaic

Branch: `design29/stage-coordinate-mosaic`

Prerequisites — none of these are code. Block 9 places pixels, so unlike Block 8 it
genuinely cannot proceed on a synthetic affine.

**Coordinator ruling (2026-07-28): these three gate the MERGE, not the branch.**
The original wording gated the branch, and that was one step too strong. Walking
the seven implementation items, the split is clean:

- *Construction* — `MosaicGeometry`, centre-based placement, bounds, resampling,
  coverage/overlap, the tool, axis selection, the manifest — is correct or
  incorrect independently of any affine's **values**. A wrong 2×2 produces a wrong
  mosaic from correct code; the Y column determines whether a given affine is
  right, not whether the placement algorithm is.
- *Verification* — the saved grid/spiral runs, landmark comparison, seam
  behaviour, the 2500-tile peak-RSS budget, and the first live exercise of Block
  8's acquisition-recorded branch — cannot be faked and stays blocked. Those
  bullets remain unticked and **no merge happens until all three prerequisites
  land**.

The one real construction risk is a *convention* error (row/col vs x/y, a sign, a
transpose) that synthetic tests written by the same agent would happily confirm.
It is mitigated structurally rather than by waiting: placement must call the
existing `StageCameraAffine.px_to_um` — Block 8 already pinned MMCore row-major
ordering by `javap` — so no matrix product is re-authored here, and the
implementer is required to assert against the **measured** `run_a_2` X column
(~90° stage/camera rotation at ~0.13 µm/px), which is real-data-derived rather
than self-consistent. Accepted risk: if the Y column, once measured, contradicts
the X column, the affine changes and the placement code does not.

Two consequences that are NOT deferrals and must land in the implementation:
per-instrument calibration identity must be **enforced**, not merely recorded
(these fixtures span three microscopes — see the table below), and the block must
leave behind a one-command peak-RSS harness so the 2500-tile measurement is a
measurement and not another project.

- [ ] Complete the convention check on the Run A datasets. The check was run:
      `run_a_1` is dark and
      rejected; the sparse revisit has no adjacent pair. `run_a_2` is PARTIAL:
      7/9 X pairs determine a ~90° row displacement at 0.1227–0.1439 µm/px,
      while Y does not cluster. Record X as a future-affine consistency check.
      **Finish this with MM's own pixel calibrator, not with new code** — see
      design/29 "Do not build a move/snap affine spike". `AutomaticCalibrationThread`
      already does the move/snap cross-correlation and `CalibrationThread.result_`
      is a full `AffineTransform`. Operator action: correct M2's blocking predicate,
      run the calibrator on a contrast-rich in-focus field, rerun our probe, and
      check the result against the `run_a_2` X column (~90°, ~0.13 µm/px). This
      bullet is an operator task, NOT an implementation task.
      **Run A is M2** (operator-confirmed), so this is a same-instrument check:
      MM's calibrator and our saved-tile correlation measure one optical path by
      two independent methods, and M2's stored 0.127 µm/px is a third. Agreement
      settles the convention; disagreement is equally informative and must be
      resolved before any of the three is trusted.
      — **DONE 2026-07-29, and it agrees.** MM's calibrator produced
      `[[0, 0.127], [-0.127, 0]]` (−90.0°, 0.127 µm/px, no reflection, no shear)
      in a new config `Res1`. Inverted, that predicts 157.5 px of row
      displacement and 0 of column displacement for a +20 µm stage X step;
      `run_a_2`'s upper mode is 157–163 px at 1–2 px of column displacement.
      Two independent methods, one optical path, agreement. **X is
      cross-validated; Y is supplied by the calibrator alone and corroborated by
      nothing** — do not record the 2×2 as verified.
      Two corrections to this bullet's own instructions: "correct M2's blocking
      predicate" pointed at the wrong thing (the mismatch is config-string
      format drift, not a misconfigured rig — a *new* config is the fix, and
      editing the live device to match a stale string would have been wrong),
      and MM's calibrator **crashed the rig repeatedly** against the old config,
      dying natively in the Andor SDK 22 ms after aborting a sequence
      acquisition. Calibrating into a new config succeeded. See design/29's
      2026-07-29 section for the CoreLog evidence and the locale mechanism.
- [x] Retrieve the spiral and 2500-tile fixtures. — DONE 2026-07-29. Both
      2500-tile scans (`scan488_900_1`, `scan561_900_1`) and the spiral are
      retrieved. **The spiral is unusable for placement**: it is 25 *separate
      single-position* datasets with no `position` axis and **no intended-XY
      metadata**, so the tool refuses it (verified). Its coordinates exist only
      in the sidecar `montage_hook_log.txt`, which is a different input
      contract and out of scope. It also has a 200 µm step against a 57.5 ×
      28.8 µm field, so the tiles never overlap — it could only ever have
      tested gaps, never seams. Spiral placement stays synthetic.
- [ ] Obtain one dataset carrying a **non-sentinel** affine. Every dataset available
      today records the all-zeros sentinel, so Block 8's acquisition-recorded branch
      is unit-tested and never field-verified. — **UNBLOCKED, not yet done.**
      `Res1` now reads `would config activate: YES`, so any small multi-position
      acquisition on M2 will stamp a real per-image `PixelSizeAffine`. This is
      the last prerequisite standing.

Implementation:

- [x] Create the branch from updated `main`. — `design29/stage-coordinate-mosaic`
      from `fecb93b`, under the merge-gate ruling above.
- [x] Add `MosaicGeometry` using the existing `StageCameraAffine`; validate finite
      coefficients, nonsingular determinant, and positive output sampling in direct
      construction. — frozen dataclass, `__post_init__` numeric invariants only;
      provenance/precedence stay in the resolver as design/29 §1 requires.
- [x] Implement centre-based `(row, col)`/`(y, x)` to `(dx, dy)` placement, transformed
      bounds, documented interpolation/rounding, `+X` right/`+Y` down output, coverage
      mask, overlap statistics, and deterministic later-tile overwrite.
      — **inverse** nearest-neighbour sampling, vectorized per tile. The first
      implementation forward-mapped source pixels and the review measured a solid
      tile rendering as alternating stripes (15 of 31 output columns zero under a
      2×/0.5× affine; ~50% loss under 21° rotation and shear), with those holes
      also reported as uncovered. Placement calls `StageCameraAffine.px_to_um`;
      only the inverse 2×2 is formed here.
- [x] Add `build_stage_coordinate_mosaic` for one explicit real value on every
      non-position axis, intended-XY metadata, tagged calibration resolution, 16-bit TIFF,
      and a hash-bearing JSON manifest with exact resolved affine payload.
      — off the acquisition ledger (pinned by a test), atomic temp+`os.replace`
      writes, and per-instrument identity **enforced**: a calibration whose
      camera device/model, ROI, or binning contradicts the dataset's own
      per-image metadata is refused with both sides named. Verified live against
      `run_a_2`, which refused a wrong camera model and reported M2's real
      `| iXon Ultra | DU897_BV | 8172 |`.
- [x] Fail without intended XY. Keep row/column reconstruction a separate explicit legacy
      mode, if implemented at all. Do not call this registration, stitching, blending, or
      biological deduplication. — row/column reconstruction deliberately NOT built;
      failure is before any artifact is written.
- [x] Test arbitrary rotations, reflections, shear/anisotropy, even/odd and non-square
      frames, overlap/gaps, spiral placement, plane isolation, invalid geometry, and
      manifest-only replay. — the first version parametrized all seven affine
      families and asserted nothing about pixel *locations* (its
      `source_sample_count == image.size` check was a tautology of the loop
      body), which is why the stripe defect was invisible; replaced with exact
      array equality against an independently written scalar reference, plus a
      no-interior-holes assertion per covered row and column.
- [-] Run on the saved grid and spiral rig datasets with zero exposures. Compare placement
      with known landmarks and record seam behavior.
      — **PARTIAL.** Grid done on two instruments, zero exposure both times:
      `run_a_2` (M2, 12 tiles, the measured `Res1` affine) builds 768×700 at
      99.86% coverage, and `scan488_900_1` (M5, 2500 tiles) builds 8576×8580 at
      100% coverage with a maximum overlap depth of 4 at tile corners.
      **Spiral is withdrawn, not deferred** — the fixture carries no intended XY
      at all (see the prerequisite above), so it can only exercise the §4
      refusal path, which it does. Spiral placement stays synthetic.
      **Landmark comparison is still outstanding** and is the one thing here
      that needs a human eye: nothing yet confirms the mosaics are *right*, only
      that they are complete, deterministic, and self-consistent.
- [x] Run the 2500-tile fixture, measure peak RSS and output correctness, and implement
      chunked/memory-mapped output before merge if it exceeds the agreed budget.
      — **8576×8580 in 3.4 s at 723 MB peak RSS**, coverage 1.0000, zero
      uncovered pixels. Comfortably within budget, so **chunked output is not
      implemented** and the design's conditional does not fire. Note the premise
      was wrong in our favour: the tiles are 180×176 on a `1336-1084-180-176`
      ROI, not full-frame 2304², so this is ~79 M cell operations rather than
      the 13 G a full-frame tiling would be. Re-measure before assuming it holds
      for full-frame tiles.
- [ ] Retrieve the design/30 spiral and 2500-tile fixtures from the rig first
      (tracked as a prerequisite at the top of this block); neither is present here. The non-square fixture is found (`run_a_1`, 453×227).
      Exact locations, recovered from the saved histories (2026-07-28) so nobody
      has to re-hunt them — note these are **two different machines and drives**,
      so it is two separate retrievals:

      | Fixture | Instrument | Path on rig | Positions | History |
      |---|---|---|---|---|
      | 2500-tile 488 | M5 | `C:\Users\ries\cell_scan\scan488_900_1` | 2500 (50×50 @ 18 µm) | `20260717_133127_..._tiling_with_nestor.json` |
      | 2500-tile 561 | M5 | `C:\Users\ries\cell_scan\scan561_900_1` | 2500 (50×50 @ 18 µm) | same |
      | 36-tile grid | M5 | `C:\Users\ries\cell_scan\cellscan_1` | 36 (6×6 @ 18 µm) | same |
      | Spiral | (spiral rig) | `F:\DataSSD\260720_PD_testMicroClaw\spiral_montage_1` | 25 (`spiral_01`…`spiral_25`) | `20260720_155559_..._pallavi_spiral_test.json` |
      | Run A grid | **M2** | OneDrive `microclaw-json-histories/run-a/run_a_{1,2}` | 12 (4×3 @ 20 µm) | `20260720_162413_..._run_a.json` |

      Both 900 µm scans are centred (1735.5, −6967.2) with mosaic 8582×8578 px —
      that is the peak-RSS target. The session's closing "cleanup" was switching
      off the 561 laser, not deleting data.

      **Instrument attribution matters here** (operator-confirmed 2026-07-28), and
      these fixtures span ~~at least three~~ **two**: Run A is **M2** (Andor iXon)
      and the tiling fixtures are **M5** (Hamamatsu). **Corrected 2026-07-29: the
      spiral is also M2**, not a third instrument — same Andor iXon DU897_BV
      serial 8172 at the same `36-50-453-227` ROI as Run A. Each instrument
      needs its OWN calibration identity; do not carry one instrument's affine to
      another's dataset. This is exactly the failure design/29 §5's camera-identity
      requirement exists to prevent, and these fixtures are the test case for it.
      Corollary: the tiling session's `pixel_size_um: 0.105` is an M5 stitcher
      parameter (and may simply have been set arbitrarily). It is NOT in tension
      with M2's 0.127 or the ~0.13 measured from `run_a_2` — those two are the same
      instrument and agree; 0.105 is a different one.
- [ ] Stop on unexplained orientation, historical-calibration ambiguity, axis leakage,
      nondeterministic hashes/pixels, or unacceptable memory. Fix and repeat.
- [ ] Commit, review, and merge.

Post-merge design gate:

- [ ] Update design/29 with interpolation/rounding details, measured seam and peak-RSS
      results, artifact format, and any explicitly unsupported cases. Update design/26 if
      the runner's promised `stage_coordinate_mosaic` input must change.

## 9b. Design/33 preparation — read-only rig inventory

Branch: `design33/read-only-rig-inventory`

Do not start this block until Block 9 is merged and its post-merge design gate is done.
This is discovery infrastructure for Block 14 Phase 5, not an early configuration
wizard and not an authorization mechanism.

- [ ] Extract the read-only enumeration machinery from
      `design/32-block7b-device-property-probe.py` into reusable production code. Keep
      the probe's defensive per-property error capture and its prohibition on mutation.
- [ ] Add `microclaw inspect-rig` with an explicit port, optional Micro-Manager `.cfg`
      path, optional existing reviewed safety config for comparison, and an output
      directory. Do not start an agent, web server, acquisition, or mutation tool.
- [ ] Emit a versioned, deterministic `inventory.json` whose schema is independent of
      `safety_config.yaml`. Record the MM config path/hash when supplied, MM/core
      identity, core device assignments, loaded devices and types, adapter identities,
      property metadata and query errors, StateDevice labels, configuration groups and
      fully expanded presets, and a hash/fingerprint of the live inventory.
- [ ] Separate mechanically observed facts from heuristic candidates and unresolved
      human decisions. Driver-reported property limits are technical ranges, never
      inferred safe limits.
- [ ] Emit `review.md` listing unclassified writable properties, suspected continuous
      actuators, illumination power/enable pairs, preset effects, enumeration failures,
      declarations missing from the live rig, and live paths missing from an optional
      reviewed config.
- [ ] If a YAML aid is emitted, make it conspicuously non-loadable (for example,
      `safety-config-template.yaml.not-ready`). It must remain `reviewed: false`, contain
      no inferred limits or approvals, and must not modify or replace an existing safety
      config. Do not add unresolved-marker keys to the strict safety schema.
- [ ] Ensure the inventory retains enough raw evidence for Block 14 Phase 2 to add typed
      actuator semantics and Phase 4 to analyze channel effects without changing the
      inventory format merely to match the safety schema.
- [ ] Test with fake cores that only approved `get_*`, `is_*`, and `has_*` bridge calls
      occur; fail the test on any setter, motion, shutter, exposure, acquisition, plugin,
      or configuration-application call. Test partial query failures, deterministic
      ordering/hashes, redaction of credentials, output confinement, and comparison with
      a stale or incomplete reviewed config.
- [ ] Run on at least M5 and one materially different rig. Retain commands, inventory,
      report, MM config hash, query failures, and a manual check that representative
      devices/properties/presets were neither omitted nor misreported as facts.
- [ ] Stop on any hardware mutation, agent/tool reachability, nondeterministic identity,
      silent enumeration loss, or candidate represented as an authorization decision.
- [ ] Commit, review, and merge.

Post-merge design gate:

- [ ] Update design/33 with the landed inventory schema, read-only boundary, measured
      cross-rig gaps, and the exact handoff to Phase 2/4/5. Keep Phase 5 responsible for
      the interactive review workflow and for writing an unreviewed safety profile.

## 10. Design/26 — shared observation writer and completed-dataset runner

Branch: `design26/completed-dataset-runner`

- [ ] Create the branch from updated `main`.
- [ ] Extract one observation writer shared by live `HookBase.log_analysis` and offline
      analysis; retain the closed statuses `unverified`, `provisional`, and `observed`.
- [ ] Implement a selection-limited, read-only `DatasetView` and bounded context exposing
      only immutable input identity, artifact directory, observation emission, and
      cancellation.
- [ ] Implement reviewed/hash-pinned saved-adapter loading for `analyze_frame` and
      `analyze_completed_dataset`. Do not replay acquisition-time `image_process_fn` as an
      offline callback unless design/26 is first explicitly revised to define a safe
      compatibility contract.
- [ ] Add generic `run_analysis_on_saved_dataset` with `frames` and
      `stage_coordinate_mosaic` input kinds; call Block 9 for the latter.
- [ ] Record dataset/selection/content hashes, analyzer source/version/environment,
      parameters, optional model/project/config, artifacts, calibration only when used,
      status, timestamps, cancellation, and failures.
- [ ] Compare deterministic scientific payload/ranking/artifacts separately from run IDs,
      paths, timestamps, and latency.
- [ ] Supply no controller, guard, acquisition, event queue, or credentials. State clearly
      that source review/hash pinning is the gate until Block 13 supplies process isolation.
- [ ] Test malicious capability requests, selection escape, artifact escape/limits,
      cancellation, per-frame and batch adapters, normalized replay, and zero hardware
      action.
- [ ] Run the design/29 provisional counting fixture over a saved mosaic and frames. Check
      that it emits provisional observations and takes no exposure or hardware action;
      do not promote it to an analysis-specific public tool.
- [ ] Commit, review, and merge.

Post-merge design gate:

- [ ] Update design/26's normative implementation brief with the exact loader, view,
      manifest, and replay contracts and measured fixture behavior. Update design/29 §3
      only where its consumer call changed. Merge docs before Run B.

## 11. Design/26 Run B — generated adapter for a real existing analysis

Branch: `design26/generated-adapter-run-b`

- [ ] Do not create the branch until an operator supplies the real workflow, a known
      input/result, target meaning, and desired initial observation-only action.
- [ ] Follow `design/26-field-spike-prompts.md` Run B and the three-question intake.
- [ ] Investigate installed files/environment/help/source/primary docs; reproduce the
      result on copied input and separately measure startup, marginal, and batch latency.
- [ ] If software/model/project is missing, **stop**. Produce a pinned installation plan
      with licenses, downloads, environment, hardware requirements, versions, and hashes;
      wait for explicit authorization. Installation is not adapter generation.
- [ ] Select direct import, another interpreter, persistent worker, executable/CLI, or
      completed-survey batch from evidence. Never accept unmeasured per-tile environment,
      JVM/application, or model startup.
- [ ] Create the branch only after the execution contract is reviewed.
- [ ] Write and fixture-test an observation-only adapter; unresolved axes, units,
      coordinates, or semantics remain `unverified`. Show source/lint and wait for explicit
      save approval.
- [ ] Run on stored data first through Block 10. Then run a fixed rig survey only after
      acquisition confirmation, retaining all Run B evidence and normalized observations.
- [ ] Permit object ranking/revisit only for verified attributed boxes/masks/centroids;
      otherwise use whole fields or report unresolved.
- [ ] Verify replay without network/adjudicator. Stop on output mismatch, lost
      attribution, cleanup leaks, timing-budget failure, or hardware capability access.
- [ ] Commit only generally reusable framework/adapter code and fixtures. Keep lab-specific
      paths, models, thresholds, and projects as pinned external/custom-hook artifacts.
- [ ] Review and merge reusable changes; retain run artifacts outside the code commit as
      appropriate.

Post-merge design gate:

- [ ] This update is mandatory: add the actual execution boundary, contract, timings,
      failures, artifact identities, scientific limits, and Run B verdict to
      `design/26-implementation.md`. Update the research record only where an empirical
      finding supersedes it. Re-plan Run C from those findings.

## 12. Design/26 Run C — optional few-shot biological classifier

Branch: `design26/few-shot-run-c`

- [ ] Skip and mark “not needed” if Run B already supplies a useful classifier or no real
      target requires learning from reviewed examples.
- [ ] Before branching, run/retain `design/26-roi-detection-spike.py` as a software
      regression, then obtain a blind real survey and reviewed positive/candidate-negative
      object crops. Do not label unreviewed crops negative.
- [ ] Create the branch after the sample, target, held-out split, and acquisition budget
      are defined.
- [ ] Implement the measured classical/enriched descriptor plus logistic probe first.
      Use LDA or another backend only after a held-out benchmark at the chosen budget
      demonstrates a reason.
- [ ] Persist `verdicts.json`, deterministic scorer artifact, and provenance-rich
      `ranked_positions.json`; preserve proposal bounds/centroids and source-tile hashes.
- [ ] Validate non-square/multi-object attribution, affine conversion, guard rejection,
      replay, contaminated/duplicate/wrong verdicts, constant features, common/absent
      targets, and explicit top-k selection.
- [ ] Run one-slide falsification first. Stop before claiming validation or driving more
      acquisition if precision, attribution, timing, drift, bleaching, or replay fails.
- [ ] Only after useful one-slide results, run multiple slides/days with slide-level
      splits. Require explicit confirmation for guarded revisits.
- [ ] Merge only reusable, accepted framework code. Do not ship a biological claim based
      on synthetic data or one slide.

Post-merge design gate:

- [ ] This update is mandatory: record Run C acceptance results and remaining deferred
      work in `design/26-implementation.md`; update `design/26-ml-roi-detection.md` where
      the backend ladder or measured claims changed.

## 13. Design/32 Finding 4 Phase 2 — generated-hook worker isolation

Branch: `design32/hook-worker-isolation`

- [ ] Create the branch from updated `main` after the design/26 adapter contracts have
      supplied real fixtures.
- [ ] Run one long-lived stateful worker instance per acquisition with a narrow,
      authenticated, length-prefixed protocol and bounded message/image sharing.
- [ ] Keep controller, guard, credentials, hardware queue, writable package source, and
      network out of the worker; apply platform filesystem/process limits.
- [ ] Enforce hard deadlines, memory limits, termination, parent survival, audit, and
      acquisition abort/cleanup. Do not claim exact analytical resume without sufficient
      checkpoints.
- [ ] Test hangs, native crashes, oversized messages/artifacts, memory exhaustion,
      malformed actions, worker restarts, stateful hooks, and parent-side progress.
- [ ] Re-run design/26 Run A and the accepted Run B stored-data fixture; run a minimal
      confirmed live Run B regression if its boundary is image-time.
- [ ] Stop if isolation changes scientific payload/replay, loses state silently, lets a
      worker reach forbidden capabilities, or takes down the parent.
- [ ] Commit, review, and merge.

Post-merge design gate:

- [ ] Update design/32's guarantees and limitations from measured failure tests. Update
      design/26 to replace its interim source-review containment caveat with the exact
      worker guarantee—no broader claim.

## 14. Design/33 later phases — separate branch and merge for each phase

Do not combine these into one branch. Repeat the branch/review/rig/design gate for each.

- [ ] **Phase 2, `design33/typed-actuator-registry`:** add only measured driver-specific
      continuous semantics/unit conversions and typed guards. Exclude unknown writes.
- [ ] **Phase 4, `design33/channel-plan-executor`:** capture one immutable expansion,
      authorize and apply those exact writes with ordering, waits, read-back, cancellation,
      rollback/safe-state behavior, and injected failure after every write. Disable gated
      presets if MM semantics cannot be reproduced safely.
- [ ] **Phase 5, `design33/first-launch-setup`:** restricted enumeration only, no agent or
      mutation tools, no inferred limits; consume Block 9b's versioned inventory, guide
      the human through every unresolved decision, write an unreviewed profile,
      disconnect, and require human review and normal restart. Do not duplicate a second
      incompatible enumeration format.
- [ ] For every phase, run its design/33 rig tests before merge and stop on any unenumerable
      effect or pre-validation write.
- [ ] After every phase merge, update design/33 with supported drivers/presets, measured
      waits/failure cleanup, setup contact semantics, and remaining excluded paths before
      beginning the next phase.

## 15. Design/32 Finding 5 — bounded context and append-only audit

Branch: `design32/context-audit-store`

- [ ] Create the branch from updated `main` (this block may be deliberately scheduled
      earlier by a human, but this single-agent checklist does not run it concurrently).
- [ ] Separate durable append-only/redacted audit from bounded model context.
- [ ] Estimate tokens before calls; compact only complete message/tool boundaries in
      infrequent batches; keep a stable structured checkpoint prefix without image bytes,
      credentials, or invented current hardware state.
- [ ] Preserve artifact references, hashes, decisions, and completed actions; require live
      hardware re-read before action.
- [ ] Add atomic transcript writes, retention controls, paginated browser history, and
      tests for crash recovery, cache behavior, tool-use/result integrity, redaction, and
      long sessions.
- [ ] Commit, review, and merge.

Post-merge design gate:

- [ ] Update design/32 with the final audit/checkpoint schema, token thresholds, retention
      controls, paging contract, and measured long-session behavior.

## Final closeout

- [ ] Re-run the full non-hardware suite and static checks on updated `main`.
- [ ] Run the smallest safe rig smoke test covering startup authorization, acquisition
      planning/ledger, Run A observation/replay, saved mosaic replay, and worker isolation.
- [ ] Confirm every rig artifact is hashed and every checklist row has a merge and design
      reconciliation result.
- [ ] Confirm no design claims validation that its acceptance gates did not establish;
      distinguish implemented, rig-plumbing-verified, scientifically provisional, and
      validated.
- [ ] Mark deferred items explicitly rather than leaving an ambiguous unchecked block.
