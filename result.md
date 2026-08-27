# Block 58d implementation report

## Commits

- `f066718` — Add cached update routes and banner
- `c6bfedb` — Prove update check production wiring

No merge or push was performed.

## Acceptance items

1. `GET /api/update` in `microclaw/webserve.py` projects only `load_state()`, selector files, and the launcher environment. It never calls a discovery provider. It reports the cached candidate, timestamps/errors, in-process staging state, pending selector, comparison refusal/reason, restart offerability, and dismissal.
2. `POST /api/update/check` calls `check_for_update(..., force=True)` in the threadpool. The ordinary startup caller uses the same function without `force`.
3. `POST /api/update/stage` uses one lock-protected in-process job flag, returns 202 for the accepted job, and 409 rather than queueing a second. The worker materializes the exact cached candidate and calls `stage_inactive_slot`.
4. `POST /api/update/restart` checks all four concrete idle states and returns an honest 501 `{restart_requested: false, pending_58e: true}` on the idle/offerable seam. It performs no shutdown.
5. `POST /api/update/dismiss` writes the minimal `dismissal` record through atomic `write_state`: `{action, commit, until}` for Later and `{action, commit}` for Skip.
6. All five routes are in `build_app` and therefore pass through the existing remote bearer/cookie middleware and cross-origin middleware. The five cases were added to the established parameter table.
7. `serve.html` polls only `/api/update`; it contains no GitHub API endpoint.
8. `#update-banner` is the first banner in `<main>`, using the existing `.banner`, heading, paragraph, and `.row` pattern. It shows short SHA/subject and the offer/progress/restart actions.
9. `stage_inactive_slot` calls the existing `compare_slot_configurations` with each slot's interpreter before pending publication. Refusal and the exact existing escape sentence are persisted and surfaced by the banner.
10. No tool/schema/context wiring was added. The identity test snapshots the registry objects, schemas and history and verifies the update route changes none of them.

The staging state additions preserve every provenance field by loading, modifying and atomically rewriting the existing object. The new fields are `staging`, `comparison_refused_commit`, `comparison_refusal_reason`, and `dismissal`; neither `git_executable` nor `remote_identity` is dropped.

## Fail-first and mutation evidence

I committed the tests, temporarily checked the six production files out from the assigned pre-change commit `5044ae9`, ran the new cases, recorded the failures below, and restored the committed implementation. The identity case is deliberately an invariant that passes on the pre-change tree, so I mutation-tested its subject instead.

- Staging composition: `Failed: DID NOT RAISE <class 'microclaw.updates.UpdateError'>`. This proves the old composition published without reaching the refused comparison.
- Cached GET/no-provider: `assert 404 == 200` at `test_update_status_reads_cache_without_calling_provider`.
- Banner markup/local API: `assert 'class="banner hidden" id="update-banner"' in html` failed.
- Single staging job: the first request failed `assert 404 == 202`, before a second job could be refused.
- Restart during turn: `assert 404 == 409`.
- Restart during acquisition: `assert 404 == 409`.
- Restart with confirmation: `assert 404 == 409`.
- Restart during setup write: `assert 404 == 409`.
- Honest idle restart seam: `assert 404 == 501`.
- Later/Skip commit scoping: the first dismissal failed `assert 404 == 200`.
- Remote-auth table, GET plus all four mutating routes: authenticated requests reached `404` instead of the endpoint-specific expected `200`/`409`; unauthenticated requests already returned 401. This demonstrates the table reached the auth middleware but the old app had no routes.
- Banner JS offer/progress/restart states: Node failed because `window.Transcript.updateBannerView` did not exist.
- Check now production call: `assert 404 == 200`.
- Startup production call/order: `ValueError: substring not found` for `target=updates.check_for_update` in `serve()`.
- Agent-state identity mutation: I temporarily appended `{"role": "assistant", "content": "update state"}` inside `GET /api/update`; the test failed with the changed history row (`At index 2 diff ... != []`). I then restored `webserve.py` from the implementation commit.

The page test covers remote subject text containing markup. The pure view returns text, and the DOM wiring assigns it with `textContent`, so the subject is not interpreted as HTML.

## Required architectural answers

### Background check (finding c)

`serve()` starts exactly one daemon thread targeting `check_for_update` before `build_session` can contact hardware. Startup and request handling do not wait for it. `check_for_update` remains the owner of platform, managed-install, 24-hour, jitter and both opt-out decisions. `--no-update-check` and `MICROCLAW_UPDATE_CHECK=0` suppress network checking only; `GET /api/update` still reads and displays cached state. Explicit Check now uses `force=True` to bypass only the due interval, not the platform or opt-out/managed-install gates.

### Authentication test correction (finding d)

The correct pattern is `tests/test_webserve.py::test_every_remote_api_route_accepts_bearer_and_cookie`, not `tests/test_host_isolation.py`. All five routes were added to that parameter table.

### Idle predicates (finding e)

- Agent turn: `session.lock.locked()`.
- Acquisition: `ctrl._microclaw_acquisition_ledger.in_flight`, backed by the ledger's active reservation count. In normal browser operation acquisitions execute inside a turn and therefore also hold `session.lock`; the acquisition predicate can nevertheless be true while the session-lock fake is false, and its independent test proves the route reads the acquisition object rather than assuming the turn proxy.
- Confirmation: `session.pending is not None`. A confirmation normally occurs inside a held turn, but is independently reachable and independently tested.
- Setup write: `ctrl._microclaw_setup_write_capability.in_flight`, set around the atomic replace and cleared in `finally`. It normally runs within a turn but is independently reachable and tested.

Thus three production conditions normally overlap the turn lock, but none is implemented as a proxy for another. Each owning object is consulted and all four route limbs have separate cases.

### Banner JavaScript choice

The pure `state -> view` function is `Transcript.updateBannerView` in `transcript.js`, beside the existing shared `initTheme` browser helper. The Node harness covers offer, progress and restart actions. DOM/event wiring remains inline in `serve.html` beside the other banner helpers.

## Test results

Focused changed-area run before the two final production-caller tests:

```text
27 passed, 1 warning in 1.36s
```

The two final caller tests then passed:

```text
2 passed, 1 warning in 0.89s
```

Full `python -m pytest -q` tail (run unsandboxed for the suite's loopback socket tests, with a temporary no-op `readline` module because this host's Miniforge `readline` extension segfaults during pytest startup):

```text
FAILED tests/test_assets.py::test_readme_png_is_not_stale - AssertionError: /...
FAILED tests/test_autofocus.py::TestSweep::test_mid_sweep_error_restores_without_masking_failed_restore
FAILED tests/test_ilastik_adapter.py::test_real_h5py_reads_all_project_and_output_keys
FAILED tests/test_ilastik_adapter.py::test_ilastiks_default_resolution_is_not_read_as_a_pixel_size
4 failed, 2284 passed, 99 skipped, 3 warnings in 120.94s (0:02:00)
```

## Could not do

- I could not produce a zero-failure full-suite result in the provided host environment. The four failures are outside this diff: a pre-existing stale derived README PNG; Python 3.10 lacking `BaseException.add_note` in an autofocus test/code path; and two h5py imports failing with `numpy.dtype size changed` binary incompatibility. The prompt forbids installing/downloading dependencies, so I did not attempt to repair the environment or regenerate unrelated assets.
- I did not run a real staging build: it requires network access for GitHub/PyPI and this sandbox has none. The worker and composition are covered with injected materialization/subprocess boundaries; no platform branch exists in route logic.
