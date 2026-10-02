# 83f-4 implementation report

Implemented in worktree `microclaw-83f-4`, branch `block-83f-4`.

Changed files: `microclaw/catalog_intake.py`; the fixture builder
`tests/fixtures/skill_packages/build_release.py`; `tests/test_catalog_intake.py`;
the repository seed under `design/83f4-catalog-repo/` (two workflows, publisher
README, empty main catalog, generated test-branch roots/policy/catalog);
`design/83-block83f4-gate.py` and its runbook;
`design/83-block83f4-mutations.py`; generated records, zips and offline evidence
under `design/83f4-gate/`; this report.

| Fixed decision | Implementation |
| --- | --- |
| H1: one publisher-signed PR file | `check`, `validate_path`, publisher signatures; release/withdrawal repository paths printed by the signing commands. |
| H2: robot merges or comments | `tree` compares bytes and executable bits; one-addition rule rejects edits/deletions/renames/extra paths. Intake workflow comments refusals, SHA-conditions squash merge, rebuilds and pushes in the same job. Both workflows share branch concurrency. |
| H3: pinned MicroClaw rules, light dependencies, no publisher execution | Single `MICROCLAW_COMMIT` setting in intake.yml; rebuild reads that setting from the base. Both refuse the placeholder. Only cryptography 44.0.2 and packaging 24.2 are directly installed. PR blobs are materialized as data; no head checkout/install/script runs. Restricted-import, no-socket and worker-marker tests cover the product path. |
| H4: separate test branch | Production roots come only from code; production `--roots` refuses, and empty production roots refuse. Test roots must declare test; policy/roots come from the base. Main has no policy; test extras are generated with public TEST-ONLY seeds. |
| H5: repository after review, operator fork gate | Seed and executable gate/runbook provided. Run creates/reuses a fork and eight PR branches; verify records and scores PR JSON, comments, runs, served catalog and isolated client fetch. Cleanup retains merged test history. Missing mechanisms score NOT EXERCISED. |
| H6: publisher commands | `keygen` creates an exclusive, secret PEM file and prints a policy key entry; `sign-release` projects the zip manifest and validates its archive; `sign-withdrawal` signs the full release identity. Gate fixtures invoke the product signing CLI. |

Direct reuse: `validate_intake`, `check_release(purpose="admission", artifact=path)`,
`verify_withdrawal`, `verify_catalog`, `verify_trust_policy`, `validate_manifest`,
`supported_executable`, `skill_store.download_release`, `skill_store._extract`,
and the existing canonical signing payload function `_canonical`. Extraction
also retains the client's `safe_release_path`, `_bound_release`, and
`verify_release_assets` checks. Gate selftest reuses 83f-3's `disk_opener` and
`isolated_store`; there are no prompts. No existing product function or caller
signature changed. Fixture `sign` delegates to the product signer;
`build_release` uses the product manifest projection while retaining the ability
to construct deliberately malformed archives for existing installer tests.

Intake also checks releases already carried by the base catalog. Build sorts
source paths, verifies zero exclusions and catalog bounds, never fetches, refuses
to drop any previous entry, and leaves an identical catalog untouched. Duplicate
version gate case D replaces A's existing filename and therefore refuses `path`
under H2 before fetching; the separate catalog-history test exercises `version`.

Validation commands:

```text
.venv/bin/python -m pytest -q tests/test_catalog_intake.py tests/test_skill_store.py tests/test_skill_packages.py
.venv/bin/python design/83-block83f4-gate.py selftest
.venv/bin/python design/83-block83f4-mutations.py
```

The targeted pytest command passed **1881 tests**, including **60 new cases**.
The gate selftest passed **12 independently scored limbs**, killed **12 per-limb
controls**, and verified missing/failed-run behavior. Code/workflow mutations:
**27/27 killed**, with **60/60 test cases failing on at least one relevant mutant**.
The full suite was not run. Complete outputs are preserved in
`83f4-gate/targeted-tests.txt`, `selftest.txt`, and `mutation-output.txt`;
`mutations.json` records exact patches, selected tests and failures.

Mutants exercised: invalid release signature; accepting extra/changed/deleted/
renamed files; accepting a forbidden collection; ignoring executable-bit changes;
missing record/path binding; following head links; bypassing root environment or
policy verification; bypassing admission; accepting a mismatched manifest URL;
skipping signer archive checks; executing the extracted publisher worker;
rewriting identical catalogs; reversed source order; dropping catalog history;
fixture signer drift; importing numpy; overwriting a private key; omitting the
printed repository path; wrong CLI refusal exit status; unbounded refusal detail;
workflow pull_request trigger, excess permissions, head checkout, head script,
and missing pin refusal; bypassing both download and admission digest checks.

Workflow shell scripts and inline Python also parse successfully. **The YAML
structural test and syntax checks are not evidence that the workflow works; the
live gate is.** No GitHub operation, network request, push or merge was performed.
The coordinator must replace the single pin, create/push the reviewed repository
seed and publish these fixture artifacts before the operator runs the live gate.
That gate remains NOT EXERCISED here, as required by this block's authorization.
