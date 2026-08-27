# Microclaw — Claude Instructions

## What Microclaw is

Microclaw helps a user build **smart acquisition workflows for their own system
and their own samples**. It is an assistant to a microscopist's session, not a
replacement for it.

- **Never anchor on one microscope.** M5, M2, the Nikon, and the demo config are
  examples, not the target. Anything in `microclaw/` must work for a generic
  Micro-Manager installation, which may drive any hardware, any device labels,
  any config groups. Rig-specific facts belong in gate docs, design notes, and
  rig profiles.
- **Microclaw is easy to use.** Prefer the smaller surface, the shorter path,
  the fewer arguments. If a feature needs a paragraph of explanation before a
  user can call it, that is a design problem.
- **The user owns the session.** Microclaw must open mid-session, and more than
  once, without assuming it set the machine up or that it is the only client.
  It augments a workflow rather than determining one, and takes charge of the
  machine only when the user asks it to. Do not add exclusive locks, ownership
  claims, or "microclaw session" state that a second launch would trip over.
- **Image analysis lives in hooks.** Analysis runs as an acquisition hook so it
  travels with the acquisition. Do not write custom image-analysis code outside
  a hook; if analysis is needed somewhere else, that is a signal the hook
  contract needs extending, not a reason to fork the code path.
- **Everything must compile to a standalone pycro-manager script.** Every tool
  call and every hook has to work as a composite inside plain pycro-manager, so
  a user can walk away with a script that runs without Microclaw. Reject designs
  that depend on Microclaw-only runtime state to execute an acquisition.

  This is implemented: `export_session_script` (block 41b, merged 2026-08-06)
  walks the session record and emits each call through an `@emits` renderer that
  lives **next to the tool it emits, never in a registry**. Analysis is inlined
  from source with `inspect.getsource`, so the emitted `snr()` *is* the one that
  ran. **One** thing is not emittable and refuses with a reason rather than
  guessing: the offline mosaic, whose dependencies reach the package calibration
  module, so inlining would not be standalone. (`set_channel` was the second
  until block 41c; it now emits both routes — the recorded device/property
  effects for the authorization-map path, the `set_config` that ran for the
  map-less one — and raises `CannotEmit` only for a call with no recorded
  result.) A tool with no
  emitter emits `# NOT EMITTED: <tool>` and a loud `RuntimeError`; a plausible
  fabrication of a step is the defect being fixed, not a fallback. The emitted
  script must import nothing from `microclaw` and is parsed before it is
  written — an exporter that hands over a file which does not compile has
  already failed.

  **Adaptive runs emit the program, not the trace** (block 43h, merged
  2026-08-11). The old refusal said their events are chosen at runtime so there
  is nothing static to render; that is true of the tiles one run happened to
  visit and false as a rule about the rule that chose them. `run_adaptive_survey`,
  and `run_timelapse` / `run_zstack` **when a hook is attached** (block 43j,
  merged 2026-08-11, folded the two `run_adaptive_*` twins into them), emit the
  seed plan, the hook's exact source, and the decision loop — `_survey_event_stream`
  and
  `UntrustedHookAdapter` inlined with `inspect.getsource`, **never re-written in
  the emitter**, because design/24 and design/27 are written into that loop and a
  hand-copied copy that drifts reintroduces ghost exposures silently. `CannotEmit`
  survives for the narrow cases: an unrecoverable hook source, an unresolvable
  seed position, a hook reaching a capability the script has no equivalent for
  (`mm_plugin_analyzer` and `autofocus_mm_plugin` take `ctrl`/`guard`), and an
  authorized illumination envelope whose conversions are rig-configured.

  **A tool that takes a hook has two emitters, and the hookless one must not
  change.** `run_timelapse` and `run_zstack` route to `_emit_adaptive` only when
  `hook_strategy` is set, and to `_emit_acquisition` otherwise — a plain SMLM
  timelapse must not start emitting an adaptive runner. Two things 43j's gate
  proved worth stating: an emitter's fallbacks are the *tool's* defaults, not
  constants (its dataset-name fallback had been the literal `"adaptive"`, right
  for the deleted twins and silently wrong afterwards, so the live run and the
  standalone script wrote differently named datasets); and every argument the
  tool accepts must reach the emitted script, because `_emit_adaptive`'s
  non-survey branches carried no exposure at all until a folded tool brought one.

  **An exported script that compiles is not an exported script that works.**
  Block 52b (merged 2026-08-17) spent **three M5 rig trips on the export alone**,
  each dying for a different reason, and two of those survived compilation *and*
  every grep in the runbook. One: a recorded error containing a newline — every
  Micro-Manager bridge exception carries a Java stack trace — broke out of its
  `# SKIPPED` comment, so `ast.parse` refused the **whole session's** export;
  pre-existing, and it meant no session could be exported on a rig where a serial
  timeout is ordinary. Two: the emitted `SimpleNamespace(core=core)` had no
  `refresh_gui`, so the script died on its first property write. Every property
  export test compiled the script and none ran it. **Write the test that execs
  the emitted source against fakes and drives `pre_hardware_hook_fn` per event**
  (`test_emitted_property_run_actually_dispatches_its_writes`); it reproduces the
  second defect exactly. And `export_session_script` compiles *this session's*
  recorded calls, so an export gate step needs a run in front of it — a fresh
  session emits a 13-line stub.

  **An exported script must also restore on its failure path** (block 52c, merged
  2026-08-17). The live runner restores after the `with` block on both the
  success and the exception path; the emitted script restored only on success, so
  a standalone run that hit a bridge fault mid-sweep left the axis parked while
  its own printed envelope had promised `restore: "entry"`. Both emitters now
  share one restoration block that runs once, after the context exits, and
  reports a restoration failure without masking the acquisition error. The same
  block closed design/52 by exporting adaptive envelopes: a survey hook's
  hook-chosen targets are *not* in the emitted script — the rule is, and on M5
  none of the run's eight recorded target values appeared anywhere in it.

  **Exported scripts print their envelope and do not prompt** (operator decision,
  2026-08-17). `Type YES to continue:` was invisible under output redirection and
  a run carrying two envelopes asked twice; the bounds, budget, guard and
  read-back are what make the script safe, and running it is the consent. The
  print stays and carries the **bound**, because it is now the only disclosure.

  **If you add a helper to `image_analysis`, the exporter must inline it.**
  `test_emitted_inline_defines_every_name_it_uses`
  (`tests/test_session_script_export.py`) enforces this. It exists
  because block 13 added `snr_validity()` while 41b was in flight: both branches
  were green alone and, merged, every exported script raised `NameError` at
  runtime on the rig.

  **If you add a tool, decorate it** — `@emits`, `@emits_nothing` or `@refuses`.
  An undecorated tool collects the default refusal, which plants a
  `raise RuntimeError` in every exported script that recorded it. That is how
  43h's second gate died: `generate_and_save_hook` was undecorated, so a session
  that wrote the hook it then used killed its own script three lines before the
  adaptive program it had correctly emitted. **Block 47's demo gate produced the
  same failure a second time** — `set_roi`/`clear_roi` were undecorated, so
  restoring ROI as a typed capability produced sessions whose exported script
  died on line 17; both now emit their bare `core` call. **Block 52a caught the
  same shape a third time, before the gate rather than during it** —
  `move_named_stage` was undecorated while sitting on the gate path of the block
  about named-stage motion. **Eleven tools are still undecorated** (measured over
  `TOOL_REGISTRY`, 2026-08-17) and are tracked in the checklist's carried-forward
  register. **A new capability is not finished until it can appear in an exported
  script** — that is the lesson all three gates taught.

## Engineering principles

- **Fold into what exists.** Before writing a new function, look for the one
  that already does this or nearly does this, and extend it. Two functions that
  do almost the same thing is a defect, not a convenience.
- **Don't add layers.** No extra wrappers, validators, registries, or guard
  passes unless the existing architecture genuinely cannot express the behavior.
  Prefer using what is already there over introducing another indirection.
- **No legacy anchoring.** This program is in active development and owes no
  backward compatibility yet. Keep an old schema, hook shape, or call signature
  working only when it is nearly free; never contort a design for it, and never
  carry a compatibility shim that costs real code. Replacing the old thing
  outright is usually the right call — say so and do it.

## Design docs and agent prompts

- Keep design documents **short and to the point**. State the problem, the
  decision, and the evidence. Include code stubs where a stub says it faster
  than prose. Long documents do not get read on the rig.
- **`design/` is for design, not for prompts.** A design doc states a problem
  and a decision. The instructions handed to an agent are working material and
  do not belong beside it.
- **Write agent runner prompts to a file in the scratchpad** before spawning the
  agent, so the exact instructions a runner received are recoverable while the
  block is live. Do not commit them. What survives the block is the *outcome* —
  the coordination notes in `design/prompts.md` (step 9), which are written after
  the fact and say what was learned, not what was asked.
- **Rig-gate runbooks are the exception and stay in `design/`, on the block's
  branch.** They are not runner prompts: the user checks that branch out on the
  rig and reads the runbook there, so step 4 of the block workflow requires them
  committed. Scratch is unreachable from the rig.

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
   worktree**. Do not code the block inline. The implementer commits and
   reports; it never merges.

   **Write the runner prompt to the scratchpad, then launch the configured Codex
   runner automatically.** Use the project `codex-runner` skill, keep its job
   directory for the lifetime of the block, and run Codex in the assigned linked
   worktree — never in the coordinator checkout. The result file is a handoff,
   not evidence: review the diff and rerun the tests in step 3. If the runner is
   unavailable or its automatic approval review cannot authorize a required
   action, stop and report instead of doing the implementation inline.

   **Two things the runner still has not proved** (2026-08-24; rechecked after
   58a, 58b and 58c, 2026-08-27 — three blocks and seven revision turns have not
   produced either condition, so keep watching rather than deleting). A revision
   turn's automatic reviewer is *inherited* from the start turn's saved session,
   not established by the wrapper's `-c` declaration; no turn has yet made a
   revision actually request an escalation, so watch the first one that does and
   confirm it is answered rather than left hanging. And `--strict-config` guards
   the revise path only, so on a machine whose `config.toml` carries a field this
   Codex version rejects, revisions fail while starts keep working.

   **What three blocks did show**: turns get killed mid-flight — five so far. A
   turn that dies *early* is discarded; one that dies *at the end*, with its
   edits landed, is preserved, committed by the coordinator with a message
   saying plainly that it is unreviewed, and handed to the next turn with that
   said in writing. Verify it yourself before believing any of it.
3. **Review what comes back.** Read the diff, not the summary. Re-run the suite
   yourself rather than accepting the reported count. Return findings to the
   implementer and repeat 2–3 until the code is right. Rejecting an
   otherwise-green implementation is normal and has caught real defects. Send
   concrete findings back through the same `codex-runner` session; do not use
   "last session", because another block or interactive run may be newer.

   **A test written after the code is not evidence until you have watched it
   fail.** Check out the pre-fix tree (`git checkout <before> -- microclaw/`),
   run the new test, confirm it fails *for the stated reason*, and restore.
   Block 52a spent three rig trips on a mechanism that had never once worked
   while its suite stayed green, because every test hand-built the input the
   engine actually strips and every fake ran callbacks the way we assumed. **A
   fake that encodes your assumption is not a test of it** — when a defect comes
   back from a rig, fix the fake before the code.
4. **Push the branch — code and runbook together.** The rig-gate runbook lives
   **on the block's branch**, not on `main`, because the user checks that branch
   out on the rig and the runbook must be in front of them for the whole run.
   Pin the implementation inside it with `git merge-base --is-ancestor <commit>
   HEAD`, never an exact tip hash, so amending the runbook cannot invalidate it.
   Push to `origin`. **Pushed, not just committed locally.** No PR.
5. **The user runs the gates** on M5, the demo machine, M2, or the Nikon. This
   is theirs. Never simulate rig evidence, and never treat a self-confirming
   probe as proof of a human boundary.
6. **The user returns the results. Score them from the artifacts, not from the
   verdict.** Read the hook log, the history JSONL, the dataset and the emitted
   script; compare numbers that should agree with each other. Block 52a's third
   gate passed every stated limb while carrying a defect that would have moved
   the stage mid-sweep — the only tell was a reported `last_known_um` that
   disagreed with the log's final achieved position. **A passing gate is a place
   to look for defects, not a reason to stop looking.** A step written as a
   criterion rather than a literal command is also a step that does not get run:
   52a's required restore limb was skipped on the very trip booked for it, while
   every lettered step around it ran. **A step must also name the *mechanism*
   under test, not the outcome** — 52b's mandatory limb asked an agent to "set
   `<stage>` `Position (um)`", it correctly used the named-stage route instead,
   and the refusal being gated never fired at all; the axis moved and the dose
   was spent for no evidence. An outcome-shaped step gets satisfied by the better
   route. And **a placeholder left in a literal command is a step that does not
   run**: 52c's export grep shipped as `Select-String -Pattern "<t2>", "<t3>"`
   with an instruction to substitute the run's values, was run verbatim, matched
   nothing, and "passed" — the block's strictest criterion produced no rig
   evidence at all and had to be checked off-rig afterwards. Write the command so
   that running it unedited either works or fails loudly.

   **And if every step of a gate is a literal command, it is a program — ship it
   as one.** Block 58a's gate went out as seven copy-paste PowerShell blocks and
   came back reporting `BLOCK 58a DEMO GATE PASSED` over five failed limbs:
   pasted interactively, a `throw` ends the current pipeline, not the session, so
   every later block ran anyway. A runbook is for steps a human performs and
   judges — driving a session, watching an optic, deciding whether a field looks
   right. Steps that only compute belong in a script that runs them all, reports
   each **independently** (one refusal must not hide five untested limbs behind
   its cascade), and exits nonzero. Three more rules that block paid for: a limb
   that could not run its mechanism reports **NOT EXERCISED** and that is never a
   pass; a limb that *cannot fail* is not a criterion, so carry a control that
   fires — 58a's opt-out limb passed three rounds by opting out inside the
   24-hour interval, where the check would not have run anyway; and the script
   owns its own log, because PowerShell 5.1's `Start-Transcript` does not capture
   a native child process's stdout and the transcript came back empty twice.
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

## The pycro-manager acquisition engine — six contracts we got wrong

The first three were found on a rig by block 52a, the fourth by block 56, the
fifth by design/56, and the sixth by design/55 — each after a full green suite.
The first five were missed because a test fake encoded our assumption instead of
the hardware's behaviour; the sixth because every test that could have caught it
supplied the one argument whose absence was the defect. Check code against these
before writing the fake, and check what the tests all happen to pass.

**And one step earlier than a misleading fake: a fixture that cannot reach the
code is not coverage of it.** Block 58a shipped a refusal guarded by
`remote_identity.startswith("github:")` while every clone fixture in the suite
used a `file:` remote. The branch was never executed — not once, through four
review rounds and 2216 green tests — and the demo machine refused the operator's
own clone on the first line. When a guard is conditional on the *shape* of its
input, ask which fixtures produce that shape, and write one that does.

- **A hook callback receives an event *or a list of events*.** When the engine
  hardware-sequences (an `interval_s=0` timelapse is exactly that shape), it
  passes the whole batch; pycro-manager's own tests assert it
  (`test_acquisition.py:421`, `:446`). Handle both, return the same shape, and
  treat a one-element list as ordinary. A **per-frame** hardware action cannot be
  honoured inside a multi-event batch — the burst runs with no software between
  exposures — so refuse it before the first exposure and tell the caller a
  nonzero `interval_s` defeats time-axis sequencing.
- **You cannot add a key to an event.** `event_to_json` / `event_from_json`
  serialise a **closed** key set (`axes`, `stage_positions`, `x`/`y`/`z`,
  `exposure`, `config_group`, `min_start_time`, `timeout_ms`, `camera`, `tags`,
  `properties`, `slm_pattern`, `special`); anything else is silently dropped on
  the way through. Identify an event by the `axes` it already carries — the one
  identity the engine must preserve, because the dataset is indexed by it — and
  never by a value you injected. Do not put a unique-per-event value *in* `axes`
  either: NDTiff readers enumerate the Cartesian product of axis values.
- **A device that is not busy is not a device that arrived.** `wait_for_device`
  returning, or `device_busy` reading false, says nothing about whether an axis
  reached the position you asked for — a stage that has not started moving yet is
  also not busy. Block 56 (merged 2026-08-19) made the single-axis move tools poll
  until the **measured** position is within tolerance **of the target** and
  stable, or raise a typed `StageMoveError`; success carries `measured_um`, never
  the requested value. **It did not reach every Z-moving path**, which is a
  standing trap: `autofocus.py` was folded in later by design/54 block 54e
  (merged 2026-08-19), and `hooks.py`'s focus-recovery jog, the tile path's
  per-position Z and `_emit_go_to_position` still move with a bare
  `core.set_position`. Before adding a Z move, check which of these it is.
  On the Nikon a successful move reported
  `last_device_status: "busy"` after 0.89 s and ~18 polls — the loop out-waited a
  device that was still moving, which is exactly the point. The defect this
  replaced was a **premature read-back**: one immediate read after
  `wait_for_device` returned the pre-move position, and a 22.85 µm miss was
  reported as a success. **Reviewed bounds are not proof that an asynchronous
  device achieved or settled at its target**, and neither is a busy flag.

- **An asynchronous *reading* settles no faster than an asynchronous move, and a
  hand-driven loop hides that behind its own latency.** Block 56 established that
  a device which is not busy has not necessarily arrived; design/56 found the
  same thing one level up, in the reading. A status property read immediately
  after the axis parks can still be reporting the previous plane — the model
  round trip in a hand-driven loop was donating a second of settling nobody had
  asked for, and collapsing that loop into one tool call took it away.
  **A device's internal rate is not its property's update rate**: the Nikon PFS
  samples at 200 Hz, and a zero dwell still misread the edge of its capture band,
  because the reading arrives through Micro-Manager's adapter on the adapter's
  cadence. Measured — dwell 0 read the band as two planes and refused it, dwell
  500 ms read three and converged. Do not infer a refresh period from an
  operation timeout (`FullFocusTimeoutMs` times a *search*, not a read), and do
  not validate a dwell against a fake you wrote to embody your own assumption.
  Where the reading only has to be right *somewhere* inside a band, a late read
  is harmless; where it decides the band's edges, it is not — design/56 §9d sets
  the default by which of those two the caller asked for.

- **A guard is only as reachable as the object it lives on.** design/52's
  refusal for a hardware-sequenced batch is a method on
  `UntrustedHookAdapter`, and block 55a (merged 2026-08-26) found that no
  coordinator is constructed when no hook is attached — so a `hook_action_plan`
  or hardware envelope passed without `hook_strategy` was discarded in silence
  and the run reported success. Four sites had used `hook_strategy` as a proxy
  for "does this run move hardware"; it is not that predicate. Before putting a
  refusal on a coordinator, hook, or adapter, ask which callers construct one —
  and refuse in the tool if any caller does not. The capability set is
  `HOOK_CAPABILITY_ARGS` in `tools.py`; **add a new capability there first**, or
  the parameterized refusal matrix generates no case for it and it is silently
  unguarded.

- **`acquire()` only submits.** It returns an `AcquisitionFuture`; completion is
  awaited in `Acquisition.__exit__` (`mark_finished()` then `await_completion()`).
  Anything that must happen after the last frame — restoration, a final
  read-back, an exit report — belongs **after the `with` block**, not after the
  `acquire()` call. Getting this wrong moves hardware while frames are still
  being taken.

## Four rules the updater block paid for

Generic, from design/58. The Windows layout details live in that document.

- **Only the thing outside both slots may write the thing outside both slots.**
  When code can be replaced and rolled back, whatever selects *which* copy runs
  must be written by something that is never itself replaced. A staged copy that
  can rewrite its own launcher can strand the machine, and a rollback then lands
  on a launcher written by the version being rolled back.
- **A marker's existence is not health.** Proof that new code started is a
  freshly written marker carrying a nonce the supervisor generated *for this
  start*, after deleting any previous one. A file that is merely present proves
  the previous run, and a check that accepts it will report a dead version as
  healthy. The same shape as the engine contract above: *a device that is not
  busy is not a device that arrived*, and — measured on the demo machine — **an
  interpreter that exists is not an interpreter that runs**, because a
  trampoline whose target is gone is still a file. Test by executing, not by
  `exists()`.
- **A gate must not leave production state pointing into its own evidence
  folder.** Block 5b's gate redirected uv's Python install directory into a
  throwaway fixture; the environment recorded that path permanently and the demo
  machine's install broke three weeks later when the fixture was deleted.
  Nothing in the suite could have caught it. If a gate redirects an environment
  variable that something durable will write down, it has planted a delayed
  failure — and the gate should check the property afterwards.
- **A subprocess inherits stdin even when you capture its output, and no test
  can have a console.** `subprocess.run(capture_output=True)` redirects stdout
  and stderr only. The desktop launcher exports `MICROCLAW_FROM_SHORTCUT=1`, so
  a CLI the server spawns registers the `Press Enter to close this window...`
  handler and blocks **after** printing its answer — measured on the demo
  machine at 0.66 s with stdin closed and a hang with an inherited console,
  which made staging impossible on every desktop launch. Pass `stdin` explicitly
  for any tool you invoke, and never let a machine-readable mode register a
  pause. The suite cannot catch this: a test runner has no console to inherit,
  so the guard is asserted on the *argument*, deliberately.
- **When a gate fails twice for reasons its own artifacts cannot explain, stop
  running the gate and write a probe.** A gate is built to score a working
  mechanism, and every phase of one drags a full setup behind it; two rig trips
  went to a defect that a one-minute, state-free script found immediately
  (`design/58-block58e-spike.py`). Relatedly, **a setup step's cost is paid on
  every retry, and retries are the normal case** — 58e's `Prepare` was copying
  two Python environments to a network-redirected folder each time.
- **When a block inserts a step *before* an existing one, test the state it
  hands over.** 58c's first gate died because migration moved `env` to `env-a`
  and the next subroutine then asked `uv venv` to create `env-a`, which refuses
  an existing environment. Both subroutines were correct; their composition was
  not, and 26 structural tests over the same file all passed.

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
