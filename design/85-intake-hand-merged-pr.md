# Intake must say when a release PR was merged by hand

**Status: 85a reviewed and gated locally, PR #54.** One block, LOCAL gate. Found on the first external
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
| 85a | `block-85a` | `70976ee` | PR #54, 2026-10-13; local gate passed; one start, one revision turn |

## What 85a built (reconciled 2026-10-13)

- `materialize` refuses `merged` before writing anything to `out`, in both
  shapes. The squash shape reached the old text through `check`, as stated above
  (confirmed on the pre-fix tree).
- For `merged`, `human()` returns the explanation alone. The first turn put it
  behind `Refused submission: field **merged**. PR head is already an ancestor
  of the base.`, which is the shape that misled the operator, so review sent it
  back. The `detail` strings survive only in the verdict JSON.
- Evidence: full suite 5779 passed, 108 skipped. With the pre-fix
  `catalog_intake.py`, all 4 new tests fail; the merge-commit test prints the
  old `no file added` refusal.
- Not covered: a PR whose head is an ancestor of the base *without* having been
  merged (an empty PR from an old commit) also reads as `merged`. GitHub does not
  open a PR with no diff, so this was left.
