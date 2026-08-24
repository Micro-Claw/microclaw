---
name: codex-runner
description: Delegate a Microclaw implementation block to headless Codex in its assigned git worktree, collect its report from the scratchpad, and resume the same Codex session for review revisions.
---

# Codex runner

Use this skill only for the implementation-agent handoff in `CLAUDE.md`'s block
workflow. The coordinator remains responsible for the checklist, ledger, diff
review, independent test run, gate scoring, merging, and cleanup.

## Start an implementation run

1. Complete block-workflow steps 1 and 2 through creation of the dedicated git
   worktree and runner prompt. The prompt must name the worktree as the place to
   work; do not tell Codex to create another one.
2. Put the prompt in a unique, uncommitted scratch job directory. Keep that
   directory for the lifetime of the block.
3. Run it in the background from the coordinator checkout. Both details are
   load-bearing: a real block outlives Claude Code's foreground Bash timeout,
   and the permission allowlist matches this relative command rather than an
   absolute path.

   ```sh
   .claude/skills/codex-runner/scripts/run-codex.sh start \
     <implementation-worktree> <scratch-job-directory> <runner-prompt>
   ```

   Invoke that command with Claude Code's `run_in_background: true`. Keep the
   returned task handle; do not replace the relative script path with an
   absolute one.

4. Continue when Claude Code reports that the background task completed, then
   read `result.md` and `status` from the job directory. Treat a nonzero status,
   a missing `result.md`, or a missing `session-id` as a failed handoff and
   inspect `initial.stderr.log` and `initial.events.jsonl`.
5. Review the work according to steps 3 onward of `CLAUDE.md`. The report is a
   handoff, never evidence in place of the diff or the coordinator's test run.

The runner requires a linked git worktree and refuses a primary checkout. It
runs Codex with the `workspace-write` sandbox and automatic approval review. It
does not use the unsafe no-sandbox bypass.

The workspace-write sandbox has no network access. If implementation requires a
dependency download, package index, or other network operation, stop and report
that limitation to the coordinator rather than retrying the same command.

## Request a revision

Write only the concrete review findings and required corrections to a new file
in the same job directory, then run this relative command in the background as
above:

```sh
.claude/skills/codex-runner/scripts/run-codex.sh revise \
  <implementation-worktree> <scratch-job-directory> <revision-prompt>
```

This resumes the explicit session in `session-id`; never use `--last`. The
wrapper pins the process to the implementation worktree. Codex CLI 0.149.1
inherits the start turn's `auto_review` reviewer from the saved session on
resume; the live start/revise probe records `auto_review` on both turns. The
wrapper also declares that same expected value under `--strict-config`, so an
incompatible CLI fails instead of appearing to retain automatic review. When
the background task completes, read the new `result.md`, review the updated
diff, and rerun the relevant tests. Repeat only while review produces actionable
findings. Stop and report to the user if Codex fails twice for the same
infrastructure reason or a revision needs new authority or a changed design
decision.

## Job artifacts

- `result.md`: latest final Codex report, replaced atomically after a successful
  turn
- `session-id`: explicit Codex session used for revisions
- `status`: exit status of the latest turn
- `initial.events.jsonl` and `initial.stderr.log`: complete first-turn records
- `revision-NNN.events.jsonl` and `revision-NNN.stderr.log`: revision records

Do not commit the scratch job directory or copy runner prompts into `design/`.
