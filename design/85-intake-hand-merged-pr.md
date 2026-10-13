# Intake must say when a release PR was merged by hand

**Status: OPEN.** One block, 85a, LOCAL gate. Found on the first external
publisher's second release, 2026-10-13.

## The problem

`Micro-Claw/package-catalog` PR #14 (`ries-lab/smappy 0.1.1`) was merged by the
operator at 14:09:02, 1 min 34 s after it opened. Intake's run started 48 s
later and posted:

> Refused submission: field **path**. path: add exactly one release or
> withdrawal file; no other changes: no file added. Correct this field and
> update the PR.

The submission was correct (re-checked off-run at the intake pin `70976ee`:
`accepted: true`). The comment addressed the publisher, named a field, and gave
no hint that the PR had been merged. The operator read it as a defect in the
publisher instructions.

The mechanism, from `catalog_intake.materialize`: a merge commit makes the PR
head an ancestor of the base, so `merge-base(base, head) == head`, the diff is
empty, and `one_added_path([])` raises the generic `path` refusal. A hand
**squash** or **rebase** merge leaves the head off the base, so the diff still
shows `A <path>`, but the blob at that path on the base is byte-identical to the
head's. Today that falls through `check`'s tree comparison into the same
"no file added" text.

A hand merge also skips intake's merge-then-`build`, so `catalog.json` is not
rebuilt: 0.1.1 was on `main` and absent from the served catalog until the
operator re-ran `check` and `build` by hand (`ca884c3`). `build` alone only
verifies signatures and policy (`verify_all`). It does not download the
artifact or check the zip, so a rebuild without a `check` first would list a
release intake never examined.

## Decision

Detect both hand-merge shapes in `materialize`, before `one_added_path`, and
refuse with their own field, `merged`, whose text says what happened and who
acts:

- **merge commit:** `merge_base == head_sha`;
- **squash/rebase:** exactly one `A` change whose path exists on the base with
  the head's blob id.

`human()` gives `merged` operator advice instead of
"Correct this field and update the PR", the way `publisher` already gets
`ADMISSION`. The comment says the PR was merged before intake checked it, that
intake did not verify it or rebuild `catalog.json`, that release PRs merge
themselves, and what the operator runs: `check` against the pre-merge base,
then `build`. The publisher has nothing to fix, and the text says so.

Not done: intake does not check and rebuild a hand-merged release
automatically. That would run `check` against a reconstructed pre-merge base
inside a job triggered by a PR that is already closed, which is a new intake
path for a mistake that a clear message prevents next time. Branch protection
that requires the `intake` check cannot be added either, because intake merges
from inside that same check.

## Blocks

**85a — the `merged` refusal.** `microclaw/catalog_intake.py` only. Acceptance:

- both shapes, built as real Git histories in `tests/test_catalog_intake.py`'s
  existing repo fixture, refuse with field `merged`;
- the rendered comment names neither a correction nor the publisher as the
  party to act, and names `check` then `build`;
- an ordinary stale-fork addition (`test_materialize_stale_fork_addition_against_current_base`)
  and a resubmitted version (`M`, "already in the catalog") are unchanged;
- a PR that adds a *different* file at an existing path is still the existing
  `M`/`A` handling, not `merged`. Only a byte-identical blob counts.

Gate: LOCAL. The tests build real Git histories, so they exercise the same
`git merge-base`/`ls-tree` calls the workflow does. After the merge, the
catalog's `intake.yml` pin moves to the merge commit (operator push).

## Run ledger

| Block | Branch | Start commit | Status |
|-------|--------|--------------|--------|
| 85a | `block-85a` | `70976ee` | opened 2026-10-13 |
