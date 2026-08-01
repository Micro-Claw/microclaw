# Microclaw — Claude Instructions

## The block workflow — authoritative

This is how every block of the active implementation checklist runs. The active
checklist is `design/35-usability-and-pfs-checklist.md`; it names the blocks and
their gates, but **this section owns the process**. Where a checklist, a design
doc, or a recalled memory disagrees with the ten steps below, this wins — say so
and fix the other document.

When the user says "you are the coordinator" for a checklist, or asks to
continue one, run these steps. Do not compress them, and do not skip a step
because a block looks small.

1. **Coordinator owns the list.** Start from updated `main`, create the block's
   branch, record the start commit in the run ledger. The coordinator alone
   edits the checklist and ledger — on a branch, like any other change, and
   **committed before the next block is assigned** (a worktree sees committed
   history, not an editor buffer).
2. **Delegate the implementation.** Hand one block, its named design sections,
   and its acceptance evidence to an implementation agent in **its own git
   worktree**. This is a standing instruction to use the Agent tool; it
   overrides the usual "don't spawn agents unless asked." Do not code the block
   inline. The implementer commits and reports; it never merges.
3. **Review what comes back.** Read the diff, not the summary. Re-run the suite
   yourself rather than accepting the reported count. Return findings to the
   implementer and repeat 2–3 until the code is right. Rejecting an
   otherwise-green implementation is normal and has caught real defects.
4. **Push the branch — code and runbook together.** The rig-gate runbook lives
   **on the block's branch**, not on `main`, because the user checks that branch
   out on the rig and the runbook must be in front of them for the whole run.
   Pin the implementation inside it with `git merge-base --is-ancestor <commit>
   HEAD`, never an exact tip hash, so amending the runbook cannot invalidate it.
   Push to `origin`. **Pushed, not just committed locally.** No PR.
5. **The user runs the gates** on M5, the demo machine, M2, or the Nikon. This
   is theirs. Never simulate rig evidence, and never treat a self-confirming
   probe as proof of a human boundary.
6. **The user returns the results.**
7. **Fix, sized to the finding.** Small corrections: do them yourself on the
   branch. Larger ones: back to a runner in a worktree, then validate its output
   as in step 3. Either way the fix is pushed to the same branch.
8. **The user re-tests.** Loop 5–8 until the gates pass.
9. **Merge and clean up.** Merge the branch to `main`, **push `main`**, then
   delete the branch locally *and* on `origin`. A block is not closed until
   `git log --oneline origin/main..main` is empty — a merge that never left the
   machine is not done. Record the block's coordination notes in
   `design/prompts.md` and close its ledger row, so a cold session can resume
   from the remote alone.
10. **Run the post-merge design gate** the block names — reconcile the design
    docs to what was actually measured, and tick the carried-forward rows. If
    documentation must change, merge that before assigning the next block.

Standing rules that support the above: one worktree per concurrent agent, never
`git add -A`, never `pip install -e .` while another tree is live. Push uses a
non-default SSH key (`GIT_SSH_COMMAND="ssh -i ~/.ssh/yonce"`). Rig-facing
commands must be PowerShell/cmd-safe. Rig facts belong in gate docs, design
notes, and rig profiles — never in `microclaw/`.

## Debugging checklist

Before proposing code changes to fix pytest failures, import errors, or
unexpected "unrecognised arguments" / plugin-loading errors, ask:

> "Have you reinstalled the package recently? (`pip install -e .`)"

A stale or broken editable install has caused confusing errors that looked
like conftest loading problems but were actually just a missing package.

## Micro-Manager ZMQ bridge (pyjavaz)

Never call `JavaClass("some.Class")` directly for **static** access. pyjavaz
caches every static-class shadow under one key (`"java.lang.Class"`), so the
first class wrapped in the process wins and later ones silently expose *its*
static methods — an order-dependent bug that passes in isolation and fails in a
full run (`AttributeError: 'java_lang_Class' object has no attribute '<method>'`).

Route all static `JavaClass` through `controller._new_static_java_class(port,
classpath)`, which evicts the colliding cache key first. `JavaObject`
(instances) is unaffected. See design/12 for the full trace.

Field access and method access use **different naming conventions** over the
bridge. A Java object's **public fields** keep their raw camelCase name —
`sp.numAxes`, NOT `sp.num_axes` (the snake_case version silently returns wrong
data / drops axes; it does not error). Static **methods**, by contrast, are
snake_cased — `create2_d`, `create1_d`. So: fields = camelCase, methods =
snake_case.
