# Automatic updates

## Problem

The Windows path asks a novice to download and extract another ZIP, rerun
`install.bat`, and remember to close the live server first. A Git checkout is
not necessarily safer to update automatically: it may be on a feature branch,
have local changes, use SSH credentials, or be an editable developer install.
Windows also holds files in the active environment open, so the process serving
the Update button cannot reliably replace itself.

An update check is a network read, not a reason to delay or disable microscope
control. Offline GitHub, rate limiting, and malformed responses must all degrade
to the currently installed version.

Three requirements shape everything below.

1. **One bootstrap, then private clone users work.** Existing users install the
   first updater-capable version once. After that bootstrap, people with a
   GitHub Desktop clone of the private repository receive updates without
   reinstalling again or being asked for a token. Today's installed environment
   cannot acquire updater code merely because its source clone changed.
2. **Public day one, and every day after.** Every updater-capable install that
   exists before the repository goes public begins updating from public `main`
   on the day it flips, with no further installer, migration or user action.
3. **The path must survive the repository moving.** Once an install is updating,
   a rename or an organization transfer must not silently cut it off.

## Decision

### Track the private clone's upstream for now

The initial users have cloned the private repository with GitHub Desktop, and
the preview intentionally wants them to follow repository head. Treat that clone
as the authenticated update source. When `install.bat` runs inside a Git
worktree, record its canonical path, repository identity, the observed branch
and upstream **as diagnostics only**, and the installed commit. Do not infer
these later from a `.git` directory that merely happens to be above the package.

**Discovery tracks `<remote>/main`, never the checked-out branch, and the remote
is `branch.main.remote` rather than whatever the current branch happens to
track.** An earlier draft of this paragraph said "current branch", which would
have retargeted the update channel the moment a gate session checked a block
branch out on the machine holding the recorded clone. Measured on the demo
machine 2026-08-26: `upstream: origin/design58/discovery`, `remote: origin`,
`tracked_branch: main`, candidate `origin/main`.

At check time run a bounded, non-interactive `git fetch` in the recorded clone,
then compare the installed commit with the upstream remote-tracking ref. Use
`GIT_TERMINAL_PROMPT=0` so startup cannot hang behind an invisible credential
prompt. The bootstrap locates and validates Git from `PATH` or the installed
GitHub Desktop bundle and records that executable; do not assume GitHub Desktop
put it on `PATH`. If that Git executable cannot reuse GitHub Desktop's
credentials, explain: **Open GitHub Desktop, Fetch origin, then Check again.**
Microclaw can still compare the remote-tracking ref GitHub Desktop refreshed.

Offer an update, named by short commit and commit subject, when the installed
commit is an ancestor of the fetched upstream commit. On acceptance, materialize
that exact commit into a new staging directory with `git archive` (or a detached
temporary worktree), then build the inactive environment from the staged copy.
Verify the staged source and slot record the fetched commit. Never modify the
user's checkout: do not fast-forward, merge, rebase, stash, discard changes,
switch branches, or stamp files there. A dirty checkout and a feature branch do
not block an update because neither is used as build input after fetch. If the
recorded remote/upstream disappears or history diverges, report it for the user
to resolve in GitHub Desktop. Never install an editable package.

This is deliberately a private-preview channel: every commit reaching the
tracked upstream may be offered to users. A protected, green `main` is part of
the updater's safety boundary — and it is a **prerequisite to ship, not a
description of today.** The block workflow merges locally and pushes `main`
directly, with no branch protection and no required check, so at this moment
that boundary is one maintainer's discipline. Turn on branch protection with a
required test run before the first update is offered to anyone. A release-note
disclaimer does not substitute for this boundary.

### Ship the public-head path at the same time

Use a commit SHA as the common update identity, with two ways to discover and
materialize it:

```text
private clone: git fetch configured upstream -> commit -> archived staging tree
public/ZIP:    GitHub public API main HEAD   -> commit -> archive for that SHA
                                                   |
                                                   v
                          build and verify the same inactive slot
```

The public provider is present in v1 even while it returns 404 for a private
repository. It queries only the compiled-in repository
`Micro-Claw/microclaw` and branch `main`, follows no URL supplied by the browser,
and downloads a GitHub archive pinned to the returned full SHA—not a mutable
`main.zip`. This requires neither Git nor a GitHub account, so a user who starts
from `Code -> Download ZIP` begins updating automatically as soon as the repo is
public, without reinstalling a newer updater first.

Installation provenance chooses the provider. A valid recorded clone uses the
clone provider. A ZIP install records `public-head` plus the compiled-in repo
identity; while the repository remains private, update status says automatic
updates will become available when public and otherwise stays silent. Do not ask
novice users for a personal access token. If a formerly recorded clone is later
deleted, offer an explicit switch to `public-head` once that endpoint is
reachable; do not silently change trust source.

Provenance lives only in `update-state.json`, which only a managed install has.
No recorded state means no update path, no banner and no CLI line — so a
developer running from a source checkout is never offered an update on the
strength of the `.git` directory that happens to sit above their package, and
off Windows the updater is simply silent, the way `microclaw/shortcut.py`
already is.

The public response must yield a 40-hex-character commit, the archive host is
allowlisted, redirects are bounded and revalidated, download/extracted sizes are
bounded, and extraction rejects absolute paths, `..`, and links. The staged
source records the requested commit and the updater writes the same immutable
commit into the staged slot only after installation and smoke checks succeed.
HTTPS and GitHub are the trust boundary
for this preview channel; there is no independent signature or checksum because
both metadata and archive originate with GitHub.

Repository identity is an immutable GitHub repository ID plus its current
owner/name, both recorded by the bootstrap installer. GitHub redirects repository
web and Git traffic after a rename or transfer, so the public provider may follow
a bounded redirect only between approved GitHub API/download hosts. It then
resolves the destination metadata and accepts the new owner/name only when the
immutable repository ID still matches. Persist that verified canonical name in
`update-state.json`; reject a redirect to a different repository. Clone-provider
fetches may follow GitHub's normal Git redirect, and discovery verifies the
fetched source against the identity **recorded at bootstrap** before staging.

**That check is name-based, not id-based, and block 58a's gate proved why it
must not be stricter.** A private Git fetch never exposes the immutable numeric
id, and this design forbids asking a novice for a token, so there is no
credential bridge to verify one. What discovery can and does detect is the
recorded remote changing *after* bootstrap — a clone repointed at another
repository. What it must **not** do is refuse a clone whose remote still names
the pre-transfer `zacsimile/microclaw`: GitHub redirects that traffic, every
GitHub Desktop clone taken before 2026-08-26 is in exactly that state, and
refusing it breaks requirement 3 rather than enforcing it. Such a clone is
accepted and the redirected name is recorded as a note that says, in the state
file, that it was not numerically verified.

The repository moved to the Micro-Claw organization on 2026-08-26, before any
of this shipped, so the compiled-in identity above is correct from v1 and no
production install ever has to follow the rename redirect. Its immutable
repository ID is **1238975695**, unchanged by the transfer and verified after
it; that is the value the bootstrap installer records. Keep the redirect
handling anyway — verification by immutable identity is what makes requirement
3 survive a later move, and trusting the owner/name alone would be unsafe if
the old `zacsimile/microclaw` namespace were reused.

Versioned, signed release metadata remains a later production channel. It can
select less-frequent updates without changing the slot, banner, restart, or
rollback machinery.

### Put the updater outside the environment it replaces

Extend the stable `%LOCALAPPDATA%\microclaw\Microclaw.cmd` launcher; the
desktop shortcut continues to target it. That wrapper is not written by
`install.bat` — it is written by `microclaw install-shortcut`
(`microclaw/shortcut.py`), which `install.bat` runs as its last step. So the
file above both slots is currently written by the package *inside* a slot, which
two slots cannot allow: a staged environment would be able to rewrite its own
launcher, and a rollback could land on a launcher written by the slot being
rolled back. **Only `install.bat` writes the external launcher files.** A slot's
`install-shortcut` keeps writing the icon and the `.lnk` and leaves the launcher
alone whenever a managed layout is present.

The wrapper also stops naming a slot. Today `_wrapper_text` bakes in the
absolute `<env>\Scripts\microclaw.exe` that `launcher()` resolved from
`sys.executable`, which is precisely the path an update has to change. Instead,
a tiny stable PowerShell launcher outside both slots owns slot selection and the
child-process lifecycle; `Microclaw.cmd` only invokes it, with `-NoProfile
-ExecutionPolicy Bypass`, because a default workstation execution policy refuses
to run a `.ps1` at all — `install.bat` already does this for the uv bootstrap.
The launcher reads two deliberately trivial text files rather than parsing
application JSON. Managed installs use two package environments:

```text
%LOCALAPPDATA%\microclaw\
  Microclaw.cmd              stable launcher/updater entry point
  updater-launcher.ps1       slot selection, child wait, health and rollback
  active-slot.txt            exactly `a` or `b`
  pending-slot.txt           absent, `a` or `b`
  update-state.json          discovery, dismissal and staging UI state
  env-a\                     active or previous known-good environment
    microclaw-slot.json      immutable commit and launcher-protocol metadata
  env-b\                     inactive staging environment
    microclaw-slot.json      immutable commit and launcher-protocol metadata
  downloads\                 bounded update staging
```

At launch, `updater-launcher.ps1` activates a valid pending slot, then starts the
active slot and waits for it. An accepted update is downloaded and installed
into the inactive slot while the old server remains usable. Run a bounded smoke
check there (`import microclaw`, CLI parse, packaged web assets present, and the
safety-config compatibility classification below). Only then atomically write
`pending-slot.txt`; never mutate the active environment.

**Restart now** requests a graceful server shutdown. After the child exits, the
still-running launcher activates the pending slot and starts `microclaw serve`
again. **Restart later** means activate at the beginning of the next desktop
launch. Closing the console may kill both launcher and child, so it is never
relied on for post-exit work. Keep the old slot until the new process writes a
bounded startup-health marker. For every child start the launcher generates a
fresh nonce, passes it in the environment, removes any old marker, and accepts
only a newly written marker containing that nonce. If the new process exits or
times out first, the launcher switches back and reports the rollback on the next
successful launch.

“Healthy” means that the selected Python and package imported, the slot metadata
matched the executing environment, the CLI plus `serve` dependencies
initialized, and the safety config was classified successfully. Missing config
is a valid setup classification; an already invalid or unreviewed file is a
valid blocked/read-only setup classification. Neither is a launcher failure.

The child writes the marker at an explicit common pre-hardware boundary in
`serve()`, immediately before `build_session(args)`. That location covers both
`Session` and `SetupSession`: first validate the launch nonce and slot metadata,
imports, serve arguments/dependencies and offline config classification; then
write the nonce-matched marker; only afterwards may `build_session` construct a
controller and contact Micro-Manager.

That classification is one immutable validation snapshot, not a check followed
by a reread:

```python
result = config.validate_safety_config(path)
write_launcher_health(result.classification)
session = build_session(args, config_result=result)
```

`build_session` branches on the supplied result without calling the validator
again. A normal `Session` receives its already parsed constraints rather than
calling `load_safety_config_or_exit`; `SetupSession` receives the same missing or
blocked result. The health marker and live session therefore describe the same
file contents even if another process edits the path during startup. This is a
hoist of the existing pure validation, not a second validator. The shortcut
launcher starts `serve`, not the REPL, so the REPL does not participate in
launcher health. Health does not require an answering bridge or other hardware
availability: a closed or slow rig is not evidence that new code is defective
and must not cause rollback. The ordinary startup path may still report that
hardware connection failure after launcher health has been established.

An update installed while the rig is closed therefore ends with the marker
written and the child then exiting on the bridge check. That is a completed
update, not a failure: the launcher keeps the new slot, does not roll back, and
does not relaunch. The user starts Microclaw again from the desktop icon once
Micro-Manager is up. Someone reading only "keep the old slot until healthy"
could as reasonably build a relaunch loop that exits every time, which is why
the outcome is written down.

An update-initiated shutdown must also suppress the shortcut's exit pause. The
`.lnk` targets `Microclaw.cmd`, and that wrapper sets
`MICROCLAW_FROM_SHORTCUT=1`, which makes `main()` register an atexit handler that
blocks on `Press Enter to close this window...`
(`microclaw/shortcut.py`), so a desktop-launched child does not exit until
somebody presses Enter — and the launcher is waiting on precisely that exit.
Left in place it stalls every **Restart now** behind a prompt in a console the
user was told to ignore. That pause exists so a novice can read a startup
refusal: keep it for ordinary exits. The server process sets
`MICROCLAW_UPDATE_RESTART=1` on itself while handling `/api/update/restart`,
immediately before requesting shutdown; the already-registered atexit handler
checks that flag when it actually runs and skips the prompt only then. The
launcher cannot pass it at spawn, because nothing knows then whether this run
will end in an update.

The external launcher has a small versioned protocol and is deliberately not
rewritten by a staged package. Each candidate declares the minimum launcher
protocol it needs; refuse staging when the installed launcher is too old and
ask for a one-time installer bootstrap. Routine application updates must remain
within the shipped protocol. This makes an exceptional future bootstrap visible
instead of letting a new slot strand or corrupt an old launcher.

The first updater-capable `install.bat` migrates the existing `env` install into
this managed layout. It records clone/upstream/commit provenance when `.git` is
present, otherwise `public-head` provenance with installed commit `unknown`
(GitHub-generated ZIPs contain no `.git` metadata). The first successful public
check may therefore offer the current HEAD once; every later comparison is exact.

The installed commit is the immutable `microclaw-slot.json` written inside each
slot after a successful install and smoke check, never a runtime guess and never
a change to the user's checkout. It contains at least the full commit SHA and
required launcher protocol. Shared `update-state.json` describes discovery,
dismissal and staging UI state; it is not authoritative about which code is
executing. A running package resolves the marker beside its own interpreter, so
activation and rollback cannot make one slot report the other slot's commit.
Nothing in the tree records a commit today and `__version__` is `0.1.0` and
stays there, because a channel that follows head has no version to compare — so
the package does not carry its own identity and the smoke check has no marker to
match. What is verified, before the build rather than after it, is that the
staged source *is* the requested commit: the clone provider archives that exact
fetched SHA, and the public provider downloads an archive pinned to the SHA the
API returned. A ZIP a user downloaded themselves from `Code -> Download ZIP`
has no Git metadata and correctly bootstraps as `unknown`.

Safety bounds under `%APPDATA%`, API credentials, histories, and user data are
outside both slots and are never migration inputs or deletion targets.

This separation is the enabling mechanism: an in-process `git pull` or `uv pip
install --upgrade` cannot provide reliable Windows replacement or rollback.

### Check quietly, ask where the user already is

The launcher starts Microclaw immediately; the active package performs at most
one background check per 24 hours (plus jitter), with short connect/read
timeouts. Cache every attempt, including private-repository 404 and transient
failure, so an unavailable source is not retried on every launch. Retain the
last successful result separately. The browser reads only local update state and
does not call GitHub directly.

For `microclaw serve`, add a dismissible banner at the top of the existing UI:

> A newer Microclaw commit is available: `4ab91cd` — Improve setup flow.
> **Update** · **Later** · **View on GitHub**

Update starts staging and changes the banner to progress, then to **Restart
now** / **Restart later**. It must refuse to restart while an agent turn,
acquisition, confirmation, or setup write is active. Restart later leaves the
pending slot for activation at the beginning of the next desktop launch.
“Later” does not ask again for that commit for seven days; settings can expose
**Skip this commit** and **Check now**.

The launcher passes a fresh launch nonce and explicit launcher-owned flag to its
child. This is lifecycle coordination, not a security credential. Offer
**Restart now** only when both are present and the pending slot is valid. A user
who directly runs `env-a\Scripts\microclaw.exe serve` may stage an update and
choose **Restart later**, but the UI must not claim it can relaunch that process
automatically.

For the terminal REPL, print one unobtrusive line after startup and ask
`Update now? [y/N]` only when stdin is interactive. Never prompt under
redirection, tests, a service, or `--no-update-check`. A CLI prompt before every
`serve` launch is rejected: desktop users may not understand the console, and
it delays the interface they do understand.

Suggested local API, loopback/auth-protected like the rest of the app:

```text
GET  /api/update             cached state only; never performs network I/O
POST /api/update/check       explicit Check now
POST /api/update/stage       begin one background staging job
POST /api/update/restart     refuse unless session is idle, then request shutdown
POST /api/update/dismiss     later or skip this commit
```

All mutating routes require the same remote authentication as other routes.
Only one staging job may run. Update state and errors are operational UI state,
not conversation history and not model tools: the agent cannot approve or
trigger its own replacement.

`--no-update-check` disables network checks for a launch, and
`MICROCLAW_UPDATE_CHECK=0` supports managed offline machines. Neither disables
using the installed version.

## Failure and trust boundaries

- A timeout, unavailable Git credentials, private/public API 404, fetch error,
  diverged upstream, invalid archive, full disk, or failed smoke check
  leaves the active slot untouched; microscope startup continues.
- **Staging needs more network than checking does.** Discovering a commit talks
  only to GitHub; building the inactive slot runs `uv pip install`, which
  resolves and downloads the dependency tree from PyPI. The likeliest real
  staging failure is therefore GitHub reachable and PyPI, a wheel build, or the
  uv cache not. Report it as "the update could not be built", keep the active
  slot, and do not retry inside the same check interval. Microclaw itself is not
  distributed through PyPI and this design does not propose that — while the
  repository is private it could not be. PyPI is only where the dependencies
  come from, exactly as it already is for today's `install.bat`.
- **An update must not invalidate reviewed safety bounds.** Classify the
  machine's config offline with both active and candidate slots. If the active
  slot accepts it as valid and reviewed, the candidate must do so too; otherwise
  the candidate is not marked `ready` and the banner says the update needs the
  maintainer. If the active slot already classifies the machine as missing-config
  setup or invalid/unreviewed blocked setup, the candidate must preserve that
  restricted classification; an update alone must not promote a previously
  blocked file into normal hardware control without a new human review. An
  update may likewise never downgrade an accepted reviewed config into either
  setup state. This comparison, rather than an unconditional candidate
  `check-config`, keeps first-launch machines updateable without weakening an
  established rig's bounds.

  Each slot must therefore be able to *report* its classification, and today it
  cannot: `check-config` prints human text and exits 1 whenever the config is
  not ready, collapsing "no file" and "file present but invalid" into one code.
  Give it a machine-readable mode that writes one JSON object containing
  `classification` (`missing`, `blocked` or `ready`) and diagnostics. Exit zero
  for all three successfully computed classifications; reserve nonzero exit for
  failure to perform the classification or emit valid output. Ask each slot's
  own CLI — the comparison means nothing unless each classification comes from
  that slot's own validator.

  The promotion guard has one deliberate cost. A machine the active slot
  classifies as blocked cannot take an update that would classify it as ready —
  right for a laxer validator, unhelpful when the candidate genuinely fixes an
  over-strict one. The escape is human review rather than a bypass: repair or
  re-review the file in setup until the active slot also classifies it `ready`,
  after which the update proceeds normally. Say that in the banner, so the
  machine does not look permanently stuck.
- Download and build run as the current user, never administrator, with bounded
  archive size, extraction that rejects absolute/`..` paths and links, and a
  subprocess argument list rather than a shell command.
- A non-fast-forward or downgrade is never offered by the preview updater.
- Never switch versions during an acquisition or unlock normal tools in a setup
  session. Closing the browser does not stop the server. Closing its console may
  kill both processes, so a pending update activates on the next desktop launch.
- Retain one known-good slot and bounded logs. Do not delete the old slot until
  the new one reaches a healthy startup marker.
- Git transport authenticates content only as far as the private repository and
  its maintainers. Protected `main` plus tested commits is required for preview
  — see the prerequisite above, which is not satisfied today; signed release
  metadata is the appropriate production follow-up.

## Delivery

1. Add the clone and public-head discovery/materialization paths, the slot
   commit marker, attempted-check caching and opt-out. Test private 404,
   malformed API/archive, dirty checkout left untouched, diverged upstream,
   missing credentials, exact-SHA selection, verified repository redirects, and
   a source checkout with no `update-state.json` offering nothing. Prove each
   process reads its own slot marker across activation and rollback.
2. Change `install.bat` and `install-shortcut` to the external two-slot
   launcher: `install.bat` alone writes `Microclaw.cmd` and
   `updater-launcher.ps1`, the launcher reads the two slot text files, and
   exact-commit installs, a machine-readable `check-config` classification mode,
   active-versus-candidate config comparison, atomic state, common
   pre-`build_session` nonce-matched health from the same validation snapshot,
   stale-marker rejection and rollback all land together.
3. Add cached status and staged-update endpoints plus the `serve.html` banner.
4. Add idle-aware shutdown/relaunch and terminal behavior, then gate on Windows:
   GitHub Desktop clone and public ZIP → upstream commit → update → restart,
   locked active files, dirty checkout preservation, failed install, failed-start
   rollback, offline launch, PyPI unreachable during staging, and preservation
   of config/key/history. Drive the restart from the desktop shortcut, not a
   terminal, so the exit pause is live and a stall is visible; prove a direct
   executable launch never offers automatic restart; and update once with
   Micro-Manager closed, which must keep the new slot without rolling back or
   relaunching.

One decision outside the code blocks the first shipped installer: protect `main`
with a required test check. Moving the repository first is simpler but optional;
immutable repository-ID verification covers a later rename or transfer.

V1 ships both providers. After users perform the one-time updater bootstrap,
authorized private clones update and private ZIPs wait harmlessly. On the day
the repository becomes public, those updater-capable ZIP installs start
discovering and installing public `main` commits with no further user action.

---

# Checklist — coordinated 2026-08-26

**Process is `CLAUDE.md` §"The block workflow", which is authoritative.** This
section owns *what* the blocks are, their gates and the ledger. `design/58` is
**not** a row in `design/35`; it tracks itself, as design/48 through design/57
each do.

Five blocks, **in sequence**. 58b is the only one that could run beside another
(it touches `config.py`/`__main__.py` and nothing the others own), and it is
still cheaper to run in order than to rebase `webserve.serve` twice.
Implementation is delegated to headless Codex through the project `codex-runner`
skill, one linked worktree per block.

## Measured before anything was assigned

- **Suite on `main` at `feb0565`, coordinator-measured: 2182 passed / 99 skipped
  / 3 warnings** (macOS). The demo machine reads the same total with a different
  skip split (2157 / 124 at the design/55 close). **Gate on zero failures, never
  the count.**
- **Repository identity verified live, 2026-08-26**: `Micro-Claw/microclaw`,
  `id` **1238975695**, `default_branch` `main`, `private: true`. That is the
  value §"Ship the public-head path" compiles in, confirmed against the API
  rather than copied forward from the transfer note.
- **`httpx` is a *test* dependency, not a runtime one** (`pyproject.toml`
  `[project.optional-dependencies].test`). The public provider gets **stdlib
  `urllib.request` and nothing else** — adding a runtime HTTP dependency to
  reach one GitHub endpoint is the layer this repo does not add, and manual
  redirect handling is required anyway by the host allowlist.
- **`install.bat` already branches on `check-config`'s exit code**
  (`"%MC_EXE%" check-config >nul 2>&1` / `if errorlevel 1`), and that branch is
  what protects an existing reviewed config from being overwritten during an
  upgrade. 58b changes `check-config`; see its item 2.

### The ship prerequisite is not merely undone — it is currently unavailable

§"Decision" makes a protected `main` with a required test check a **prerequisite
to ship**, not a description of today. Measured 2026-08-26, it cannot be turned
on at all:

```
GET /repos/Micro-Claw/microclaw/rulesets            -> 403
GET /repos/Micro-Claw/microclaw/branches/main/protection -> 403
   "Upgrade to GitHub Pro or make this repository public to enable this feature."
```

`Micro-Claw` is a **Free** organization and the repository is **private**;
branch protection and rulesets are unavailable in that combination. And there is
**no `.github/` directory and no workflow in the tree**, so there is no test
check to require even if protection could be enabled.

This blocks **shipping the first updater-capable installer to a user** — 58e's
merge — and blocks nothing before it. Three escapes, in 58-P below. A release
note is explicitly not one of them; the design already rules that out.

## The gate plan — every gate is the demo machine

**The operator has lost access to the Nikon; available machines are the demo
machine, M2 and M5.** That costs this design nothing, and it is worth saying
exactly why rather than asserting it.

- **Nothing here touches hardware.** The updater discovers a commit, builds an
  environment, swaps a text file and restarts a process. The one rig-adjacent
  contract in the whole document — §"Put the updater outside the environment it
  replaces", an update completed with Micro-Manager **closed** keeps the new slot
  and does not relaunch — requires Micro-Manager *closed*, which the demo
  machine supplies by not opening it.
- **The demo machine is Windows, which is the only platform this ships on.** Off
  Windows the updater is silent by design. Every gate below is PowerShell.
- **A rig is the wrong machine for this gate anyway.** These steps replace the
  installed environment, kill the server and force rollbacks, repeatedly. Doing
  that on M2 or M5 buys nothing and spends rig time.

**One optional limb is worth M2 or M5, and it is read-only and zero-dose**:
58b's promotion guard against a *fuller* reviewed config than the demo machine's
minimal schema-3 document. It is a single `check-config --json` invocation, needs
no acquisition and no Micro-Manager, and is not a precondition of merging.

**Every command block in every runbook runs unedited.** No `<placeholder>` a
reader is meant to substitute — 52c's strictest criterion produced no evidence
because one shipped with `Select-String -Pattern "<t2>", "<t3>"` and was run
verbatim. A step that prints nothing where a match is required has **failed**.

## 58-P — a protected `main` with a required check

**Not a code block, and not assignable to a runner.** It is an operator decision
plus a small amount of repository configuration, and it gates 58e's merge only.

- [ ] **Decide the escape.** Exactly one of:
      **(a)** upgrade `Micro-Claw` to GitHub Team, which makes rulesets available
      on a private repository;
      **(b)** hold the first shipped installer until the repository is public,
      at which point protection is available on the Free plan;
      **(c)** amend §"Decision" — *not* a release note — to state what boundary
      replaces it and why that is acceptable for the preview channel.
- [ ] **There must be a check to require.** Add `.github/workflows/tests.yml`.
      It does not exist today; Actions itself is enabled on the repository
      (`{"enabled": true}`, verified 2026-08-26).
- [ ] **Linux and Windows, not macOS.** Runner minutes are billed with
      multipliers — Linux 1x, **Windows 2x, macOS 10x** — against the 2,000
      minutes a month GitHub Free includes for an organization's *private*
      repositories. The suite is ~108 s locally, so call a job five minutes with
      dependency install: Linux + Windows is ~15 charged minutes a run and
      comfortably over a hundred runs a month, while adding macOS would cost ~50
      a run on its own and cap the month near forty. **Windows is the only
      platform this project ships on**, and the coordinator measures macOS
      locally at every block anyway — that is where the recorded baseline comes
      from. Public repositories get Actions free and unlimited, so this
      constraint dissolves the day the repository flips.
- [ ] **It must not run on our merges. Trigger on `pull_request` and
      `workflow_dispatch`, and on nothing else.** This block workflow branches
      and merges constantly and every block is tested locally first, so a
      `push` trigger would burn the allowance on merges that were already green
      on the coordinator's machine and on the operator's. With no `push:` key
      the workflow runs **zero** times automatically today, because there is no
      PR flow — it is a dormant file that costs nothing until the 58-P decision
      creates the PR flow that needs it, and `workflow_dispatch` means it can
      still be fired by hand whenever a run is actually wanted. **Adding it now
      is therefore free of both money and noise**; that is the whole reason it
      can land ahead of the protection decision.
- [ ] **If a path filter is added, add the companion job with it.** Most merges
      here are documentation — every `coord/*` branch in this design's own
      history is docs-only — so `paths-ignore` on `design/**`, `docs/**` and
      `**.md` is tempting. The trap: a **required** check skipped by a path
      filter is never reported, and GitHub leaves the PR waiting on a status
      that will never arrive. Pair any filter with a job of the same name that
      reports success on the filtered paths, or do not filter at all.
- [ ] **Confirm the spending limit reads $0** before enabling: Organization
      settings -> Billing -> Spending limits. At $0 an exhausted allowance stops
      queueing workflows rather than billing anything, and no overage is possible
      without both raising that limit and attaching a payment method. Verify it;
      do not assume it.
- [ ] **Then require it** on `main`, with the maintainer included in the rule.
      Record here that it is on, with the ruleset id.
- [ ] **Requiring a check changes step 9 of the block workflow, and that is a
      decision, not a side effect.** A required status check enforces **only on
      pull requests**; protecting `main` blocks the direct push that
      `CLAUDE.md` step 9 performs today ("merge the branch to `main`, push
      `main`"). So protection converts every block merge into open-a-PR,
      wait-for-green, merge — which also cuts against the standing "no PR until
      integration" preference. Adding CI costs nothing and settles nothing here;
      decide the PR flow deliberately before 58e, not at its merge.

## 58a — provenance, discovery, and materializing an exact commit

**Branch:** `design58/discovery`. **Design sections:** "Track the private clone's
upstream for now", "Ship the public-head path at the same time", the
`update-state.json`/`microclaw-slot.json` paragraphs of "Put the updater outside
the environment it replaces", "Check quietly, ask where the user already is"
(caching and opt-out only), "Failure and trust boundaries" (first, third and
fourth bullets), "Delivery" step 1.

**One new module, `microclaw/updates.py`.** No package, no provider registry, no
abstract base class: two functions that return the same `Candidate` record are
two functions. `CLAUDE.md` §"Don't add layers".

### Items

- [ ] **1. Compiled-in identity, in one place and not configurable.**
      `REPO_ID = 1238975695`, `REPO = "Micro-Claw/microclaw"`, `BRANCH = "main"`.
      No environment override, no config key, no value reachable from the
      browser. §"Ship the public-head path" is explicit that the provider
      "follows no URL supplied by the browser".
- [ ] **2. `update-state.json`, written atomically**, holding provenance
      (`clone` or `public-head`), the recorded clone path / repo identity /
      branch / upstream / **git executable**, the verified canonical owner/name,
      and the discovery, dismissal and staging UI state. **It is not
      authoritative about which code is executing** — that is the slot marker.
- [ ] **3. No recorded state means no update path.** No `update-state.json` →
      no check, no banner, no CLI line. A developer running from a source
      checkout is never offered an update on the strength of a `.git` directory
      above their package. Off Windows the module is silent, as
      `microclaw/shortcut.py` already is.
- [ ] **4. Git is located and recorded, not assumed.** `PATH` first, then the
      installed GitHub Desktop bundle. Do not assume GitHub Desktop put `git` on
      `PATH`. Record the executable that was validated.
- [ ] **5. Clone discovery: bounded, non-interactive, read-only.**
      `git fetch` as a subprocess **argument list**, with `GIT_TERMINAL_PROMPT=0`
      and a timeout, in the recorded clone. Compare the installed commit against
      the remote-tracking ref. Offer the update only when the installed commit is
      an **ancestor** of the fetched upstream commit — a non-fast-forward or a
      downgrade is never offered.
- [ ] **6. The user's checkout is never modified.** No fast-forward, merge,
      rebase, stash, discard, branch switch, or stamped file. A dirty tree and a
      feature branch do not block an update, because neither is build input.
- [ ] **6a. Discovery tracks `<remote>/main`, never the checked-out branch.**
      §"Track the private clone's upstream" says the bootstrap records the
      clone's *current branch*; on our own machines that is wrong and it bites
      immediately. The demo machine's clone is the one an operator checks a block
      branch out onto to run a gate, so a bootstrap performed during a gate — or
      a gate run after a bootstrap — would silently retarget the update channel
      at `design58/two-slots`. Resolve the remote-tracking ref for the
      compiled-in `BRANCH` at **check** time. Record the observed branch and
      upstream as diagnostics if they are useful in an error message; never as
      the thing that selects the commit. **Amend line 36 of this document** in
      the post-merge design gate.
- [ ] **7. Credentials that do not work produce the sentence, not a hang.**
      "Open GitHub Desktop, Fetch origin, then Check again." Microclaw still
      compares the remote-tracking ref GitHub Desktop refreshed.
- [ ] **8. Clone materialization is `git archive <sha>`** into a fresh staging
      directory (or a detached temporary worktree). The staged source records the
      requested SHA, and that is verified **before** the build.
- [ ] **9. Public discovery through stdlib `urllib.request`.** One API call for
      `main`'s HEAD, short connect and read timeouts. The response must yield a
      **40-hex** commit; anything else is a malformed response, not a candidate.
- [ ] **10. Public materialization is pinned to that SHA** — an archive for the
      returned full commit, never a mutable `main.zip`.
- [ ] **11. Host allowlist and bounded, revalidated redirects.** Automatic
      redirects **off**; follow manually, at most a small fixed number, and
      revalidate the host at every hop against the GitHub API/download hosts.
- [ ] **12. Redirect acceptance is by immutable id.** Resolve the destination
      metadata and accept a new owner/name **only** when `REPO_ID` still matches;
      persist the verified canonical name into `update-state.json`; reject a
      redirect to any other repository. Keep this even though the transfer is
      already done — it is what makes requirement 3 survive a later move, and the
      vacated `zacsimile/microclaw` namespace could be reused by anyone.
- [ ] **13. Extraction is hostile-input handling.** Bounded download size,
      bounded extracted size, and members rejected for absolute paths, `..`,
      symlinks, hardlinks and device entries. Run as the current user, never
      elevated.
- [ ] **14. Every attempt is cached, including the failures.** A private-repo
      404 and a transient error both cache, so an unavailable source is not
      retried on every launch. Retain the last **successful** result separately.
      At most one background check per 24 hours plus jitter.
- [ ] **15. Opt-out.** `--no-update-check` for a launch and
      `MICROCLAW_UPDATE_CHECK=0` for a managed offline machine. Neither disables
      *using* the installed version. (The flag is parsed here; the REPL line and
      prompt it suppresses arrive in 58e.)
- [ ] **16. `microclaw-slot.json` is written and read beside the running
      interpreter.** At least the full commit SHA and the required launcher
      protocol. Resolve it from `sys.executable`, never from shared state, so
      activation and rollback cannot make one slot report the other's commit.
      Nothing in the tree records a commit today and `__version__` stays
      `0.1.0` — a channel that follows head has no version to compare.
- [ ] **17. A ZIP install bootstraps as `unknown`** and the first successful
      public check may therefore offer the current HEAD once. Every later
      comparison is exact.
- [ ] **18. No agent tool, no schema, no emitter.** Nothing in this block reaches
      `TOOL_REGISTRY`, `tools_schema.py` or `export_session_script`. Update state
      is operational UI state, not conversation history and not a model tool: the
      agent cannot approve or trigger its own replacement.

### Tests — watch each one fail on the pre-change tree

`git checkout <before> -- microclaw/`, run, confirm it fails **for the stated
reason**, restore. The implementer does it; the coordinator re-verifies it. The
exception is anything asserting existing behaviour is *unchanged* — a regression
test is supposed to pass on both trees, and demanding it fail manufactures fake
evidence (`feedback_watch_it_fail_not_regressions`).

- [ ] A private-repository 404 yields no candidate, caches the attempt, and
      leaves the last successful result alone.
- [ ] A malformed API body, a non-40-hex `sha`, and a truncated archive each
      refuse with a distinguishable reason.
- [ ] An archive member with an absolute path, one with `..`, and one that is a
      symlink are each rejected; a plain oversize archive is rejected before it
      is written out.
- [ ] A redirect to a repository whose `id` differs is **rejected**; a redirect
      to a different owner/name whose `id` matches is accepted and the canonical
      name is persisted. A redirect to a non-allowlisted host is rejected at the
      hop, not after the download.
- [ ] A dirty checkout on a feature branch still produces a candidate, and the
      checkout is **byte-identical** afterwards — status, HEAD, branch and
      worktree.
- [ ] A diverged upstream (installed commit not an ancestor) offers nothing.
- [ ] A clone on a feature branch, on a detached HEAD, and on a branch with no
      configured upstream each still discover `<remote>/main`'s commit — three
      cases, because each fails a different naive implementation.
- [ ] A `git fetch` that would prompt exits non-zero under
      `GIT_TERMINAL_PROMPT=0` and produces the GitHub Desktop sentence, bounded
      by the timeout.
- [ ] `git archive` staging materialises the **exact** requested SHA, verified
      against the tree, and a mismatch refuses before any build.
- [ ] A source checkout with no `update-state.json` offers nothing, with a `.git`
      directory sitting right above the package.
- [ ] On a non-Windows platform every entry point returns silently.
- [ ] Two slot markers with different SHAs: a process resolving from its own
      `sys.executable` reports its own, across a simulated activation **and** a
      simulated rollback.
- [ ] The 24-hour interval is honoured, jitter is applied, and a cached failure
      suppresses the retry.
- [ ] `TOOL_REGISTRY` and the tool schemas are unchanged — an identity test, so
      it goes red the moment an update verb becomes a model tool.

### Gate — demo machine, `design/58-block58a-demo-gate.md`

The first exercise of real Git, real network and the real private repository.
No Micro-Manager needed.

- [ ] Discovery against the demo machine's own clone finds a commit that equals
      its `git rev-parse origin/main`, printed side by side.
- [ ] The public provider **404s** on the private repository, the attempt is
      recorded in `update-state.json`, and the status line is the silent one.
- [ ] A file is dirtied and a branch checked out in the clone before the check;
      afterwards `git status --porcelain` and `git rev-parse --abbrev-ref HEAD`
      are unchanged, printed before and after.
- [ ] A materialized staging tree's recorded SHA matches the fetched commit.
- [ ] **A clone whose remote still names the pre-transfer repository is
      accepted, and discovery still reaches `origin/main`.** Requirement 3, and
      round 1's finding. The demo machine is the fixture; do not repoint it.
- [ ] **A block branch is checked out in the clone, and discovery still names
      `origin/main`'s commit** — the branch is `design58/discovery`, the reported
      candidate is `git rev-parse origin/main`, and the two are printed together.
      This is the limb our own gate sessions would otherwise break.
- [ ] `--no-update-check` and `MICROCLAW_UPDATE_CHECK=0` each perform no network
      call, proven by the unchanged attempt timestamp in `update-state.json`.

## 58b — one classification, reported by each slot, from one validation snapshot

**Branch:** `design58/classification`. **Design sections:** "Failure and trust
boundaries" bullet 3 in full, and the `result`/`build_session` snippet in "Put
the updater outside the environment it replaces".

### Items

- [ ] **1. `check-config --json` writes exactly one JSON object to stdout**, with
      `classification` (`missing`, `blocked`, `ready`), the path, and the existing
      diagnostics. **Exit 0 for all three successfully computed classifications**;
      reserve non-zero for failing to classify or to emit valid output. Nothing
      else may be printed on stdout in this mode.
- [ ] **2. The human mode's exit codes do not change.** `install.bat` runs
      `check-config` bare and branches on `errorlevel 1` to protect an existing
      config from being overwritten during an upgrade. Inverting that branch
      would let an upgrade overwrite a reviewed rig's bounds. Pin it with a test.
- [ ] **3. No second validator.** The classification is derived from the existing
      `ConfigValidationResult` — `parsed is None and not path.exists()` →
      `missing`; `can_start_live_validation` → `ready`; everything else →
      `blocked`. `validate_safety_config` keeps its signature and its behaviour.
- [ ] **4. Hoist the validation, do not duplicate it.**
      `build_session(args, config_result=result)` branches on the supplied
      result. `Session.__init__` receives already-parsed constraints instead of
      calling `load_safety_config_or_exit`; `SetupSession` receives the same
      result. One immutable snapshot, so the health marker and the live session
      describe the same file contents even if another process edits it during
      startup. `build_session(args)` with no result keeps working — it computes
      the snapshot itself, once.
- [ ] **5. The comparison is active-versus-candidate, not an unconditional
      candidate `check-config`.** `ready→ready` proceeds. `ready→missing` and
      `ready→blocked` refuse: an update may never downgrade an accepted reviewed
      config into a setup state. `blocked→blocked` and `missing→missing` proceed,
      which is what keeps first-launch machines updateable. `blocked→ready` and
      `missing→ready` **refuse** — an update alone must not promote a
      previously blocked file into normal hardware control without a new human
      review.
- [ ] **6. The refusal names the escape.** "Repair or re-review the file in setup
      until this version also classifies it `ready`, then the update proceeds" —
      not a bypass, and not a message that makes the machine look permanently
      stuck.
- [ ] **7. Each classification comes from that slot's own CLI.** The comparison
      means nothing if one validator classifies both files. Invoke each slot's
      own `microclaw.exe check-config --json` as a subprocess.

### Tests

- [ ] The **nine-cell** matrix of active × candidate classification,
      parameterized, each with the expected proceed/refuse and, for the two
      promotion cells, the escape sentence.
- [ ] `check-config --json` exits **0** for `missing`, `blocked` and `ready`, and
      its stdout parses as exactly one JSON object in each.
- [ ] `check-config --json` exits non-zero when classification itself fails, and
      emits no half-object.
- [ ] Human `check-config` still exits 1 on unreviewed and 0 on ready —
      *regression*, so do not stage a failing run for it.
- [ ] A structural assertion in `tests/test_installer.py` that `install.bat`
      still runs `check-config` **without** `--json` and branches on
      `errorlevel`.
- [ ] `build_session` calls `validate_safety_config` **once** — spy on it and
      assert the call count, for both the `Session` and `SetupSession` routes.
- [ ] A config replaced on disk between the snapshot and `build_session` does not
      change what the session receives.

### Gate — demo machine, its own program

**Not folded into 58c, and not a runbook.** Every limb here is a literal
command, so it ships as `design/58-block58b-demo-gate.py` plus a thin `.ps1`,
exactly as 58a's does after its three failed rounds — `uv run` throughout, one
PASS / FAIL / NOT EXERCISED record per limb, its own log, nonzero exit unless
every limb ran and passed. Folding it into a later block's gate would delay
58b's only hardware evidence behind the heaviest block in the design.

No hardware, no Micro-Manager:

- [ ] `check-config --json` on the machine's real config → `ready`,
      `$LASTEXITCODE` **0**.
- [ ] The same against a copied-aside path that does not exist → `missing`,
      `$LASTEXITCODE` **0**.
- [ ] The same against a copy with `reviewed: false` → `blocked`,
      `$LASTEXITCODE` **0** — and bare `check-config` against that same copy
      still prints `$LASTEXITCODE` **1**.
- [ ] **Optional, M2 or M5, read-only and zero-dose.** One
      `check-config --json` against that rig's fuller reviewed config, which
      carries the `plugins` and actuator sections the demo machine's minimal
      schema-3 document does not. Not a precondition of merging. Copying the
      file to the demo machine instead is equally good evidence and costs no rig
      time.

## 58c — two slots and a launcher outside them

**Branch:** `design58/two-slots`. **Design sections:** "Put the updater outside
the environment it replaces" in full, "Failure and trust boundaries" bullets 5
and 6, "Delivery" step 2.

The block that can brick an install. It carries the migration.

### Items

- [ ] **1. Only `install.bat` writes the external launcher files** —
      `Microclaw.cmd` and `updater-launcher.ps1`, sourced from beside itself
      (`scripts/`). A slot's `install-shortcut` keeps writing the icon and the
      `.lnk` and **leaves the launcher alone whenever a managed layout is
      present**. A staged environment must not be able to rewrite its own
      launcher, and a rollback must not land on a launcher the rolled-back slot
      wrote.
- [ ] **2. The wrapper stops naming a slot.** `_wrapper_text` currently bakes in
      the absolute `<env>\Scripts\microclaw.exe` that `launcher()` resolved from
      `sys.executable` — precisely the path an update has to change.
      `Microclaw.cmd` becomes `MICROCLAW_FROM_SHORTCUT=1` plus an invocation of
      `updater-launcher.ps1` with `-NoProfile -ExecutionPolicy Bypass`, because a
      default workstation execution policy refuses to run a `.ps1` at all.
      **The non-managed developer install keeps today's behaviour** — that path
      has no slots and must not acquire them.
- [ ] **3. The launcher reads two trivial text files**, `active-slot.txt`
      (exactly `a` or `b`) and `pending-slot.txt` (absent, `a` or `b`). It does
      not parse application JSON. Keep it thin: slot selection, child lifecycle,
      health, rollback, and nothing else.
- [ ] **4. Activation happens at launch**, before the child starts: a valid
      pending slot is activated, then the active slot runs.
- [ ] **5. Fresh nonce per child start.** The launcher generates it, passes it in
      the environment, **removes any old marker first**, and accepts only a newly
      written marker containing that nonce. A stale marker from the previous run
      is a failure, not a pass.
- [ ] **6. Health is written at a common pre-hardware boundary in `serve()`,
      immediately before `build_session(args)`** — after validating the launch
      nonce, the slot metadata against the executing environment, the imports,
      the serve arguments and dependencies, and the offline classification;
      never after. It covers both `Session` and `SetupSession`.
- [ ] **7. Health does not require an answering bridge.** A closed or slow rig is
      not evidence that new code is defective and **must not** cause rollback.
- [ ] **8. An update completed with Micro-Manager closed is a completed update.**
      The marker is written, the child then exits on the bridge check, and the
      launcher **keeps the new slot, does not roll back, and does not relaunch**.
      The user starts Microclaw again from the icon. Someone reading only "keep
      the old slot until healthy" could as reasonably build a relaunch loop that
      exits every time; this outcome is written down because it is not derivable.
- [ ] **9. Rollback.** If the child exits or times out before a nonce-matched
      marker, the launcher switches back and reports the rollback **on the next
      successful launch**.
- [ ] **10. Keep one known-good slot.** Do not delete the old slot until the new
      one reaches a healthy startup marker. Bounded logs.
- [ ] **11. Versioned launcher protocol.** Each candidate declares the minimum
      protocol it needs; **refuse staging** when the installed launcher is older
      and ask for a one-time installer bootstrap. Routine updates stay within the
      shipped protocol.
- [ ] **12. Migration.** The first updater-capable `install.bat` moves the
      existing `env` into the managed layout as `env-a`, writes `active-slot.txt`,
      writes `microclaw-slot.json`, and records provenance — clone/upstream/commit
      when `.git` is present, otherwise `public-head` with commit `unknown`.
- [ ] **12a. Migration covers `%LOCALAPPDATA%\microclaw\env` and nothing else,
      and it must say so out loud.** `microclaw/shortcut.py:launcher()` handles
      two layouts on purpose and its comment names the second: *"Under conda —
      what the lab machine runs — python.exe sits at the env root."* A conda,
      miniforge or embedded-Python install is not at the migrated path, so
      `install.bat` builds a **second**, uv-managed installation beside it and
      `install-shortcut` moves the desktop icon to the new one — leaving the old
      environment orphaned, still holding old code, with nothing telling the user
      it is no longer what the icon launches. Detect that case (a resolvable
      `microclaw` outside the managed layout) and **print what happened and where
      the old environment is**. Do not delete it, do not import from it, and do
      not attempt an in-place conda upgrade.
- [ ] **12b. The updater never installs into an environment it did not create.**
      Every `uv pip install` targets a slot under `%LOCALAPPDATA%\microclaw`
      that this installer or this updater made. No path derived from
      `sys.executable`, `CONDA_PREFIX`, `PATH`, or a recorded clone may ever
      become an install target. This is the item that answers "will it break my
      miniforge environment" in code rather than in prose.
- [ ] **12c. An unmanaged install stays silent, not broken.** With no
      `update-state.json` it gets no check, no banner and no CLI line (58a item
      3) and is left byte-untouched. That is the shipped behaviour for every
      conda and embedded-Python user who never runs the new `install.bat`, and it
      is the behaviour to keep.
- [ ] **13. `%APPDATA%` is out of scope, in both directions.** Safety bounds, API
      credentials, histories and user data are never migration inputs and never
      deletion targets.
- [ ] **14. Never install an editable package**, in any slot, on any path.
- [ ] **15. Staging needs more network than checking does.** `uv pip install`
      resolves the dependency tree from PyPI; the likeliest real failure is
      GitHub reachable and PyPI not. Report "the update could not be built", keep
      the active slot, and **do not retry inside the same check interval**.

### Tests

The `.ps1` cannot execute on the CI platform, so split the evidence deliberately
rather than testing only what is convenient:

- [ ] **The state machine is Python and is unit-tested**: activation, pending
      consumption, nonce generation and matching, stale-marker rejection, slot
      metadata that names the *other* slot, rollback, and the protocol-too-old
      refusal — as pure functions over the two text files and the marker.
- [ ] **The `.ps1` and `.cmd` are structurally tested** in
      `tests/test_installer.py`'s existing style: `-NoProfile
      -ExecutionPolicy Bypass` present, no slot name baked into `Microclaw.cmd`,
      every branch reachable, and the two files written by `install.bat` and by
      nothing else. Grep `microclaw/` for a writer of either filename and assert
      there is none.
- [ ] `shortcut.install` on a machine with a managed layout writes the icon and
      `.lnk` and **does not touch** the launcher; without one it behaves exactly
      as today.
- [ ] `serve()` writes the marker **before** `build_session` — assert ordering by
      instrumenting both, not by reading the source.
- [ ] A `build_session` that raises on the bridge **after** the marker is written
      leaves the marker in place (item 8, the shape that decides relaunch).
- [ ] A wrong or missing nonce means no health, and the marker file's mere
      existence is never sufficient.
- [ ] Migration preserves `%APPDATA%` config, credential file and histories
      byte-for-byte, and never reads them as inputs.
- [ ] A staging build whose `uv pip install` fails leaves the active slot and
      `active-slot.txt` untouched and writes no `pending-slot.txt`.

### Gate — demo machine, `design/58-block58c-demo-gate.md`

The heavy one. It carries 58b's three commands as its first step. **Back up
`%APPDATA%\microclaw` and the existing `%LOCALAPPDATA%\microclaw\env` before
Step 1** — the runbook says so as a literal command, and prints the copy.

- [ ] An existing `env` install migrates to `env-a`, `active-slot.txt` reads `a`,
      and `microclaw-slot.json` carries the checkout's commit.
- [ ] The desktop icon launches through `updater-launcher.ps1` and the child is
      `env-a`'s executable — proven from the process command line, not inferred.
- [ ] The health marker exists, carries the launch nonce, and was written before
      the bridge was contacted.
- [ ] **Micro-Manager closed**: the marker is written, the child exits on the
      bridge check, the launcher keeps the slot, does not roll back, and does not
      relaunch. This is the limb most likely to be built as a relaunch loop.
- [ ] A slot deliberately made to fail its start rolls back, and the rollback is
      reported **on the next launch**, not on the failing one.
- [ ] `%APPDATA%` config, key and histories are unchanged, printed as hashes
      before and after.
- [ ] `install.bat` run twice in a row is idempotent and does not lose the
      active slot.
- [ ] **58b's owed limb: two real slot validators classify one shared config
      file.** 58b's gate drove all nine guard cells through a gate-written stub
      and reported this NOT EXERCISED because no second slot existed. 58c builds
      one — run `compare_slot_configurations(env-a CLI, env-b CLI, <one config>)`
      and record both classifications. This is the only debt 58b carried
      forward, and 58c is the block that discharges it.
- [ ] **A non-uv environment is left alone.** Create a throwaway conda/venv
      environment with microclaw installed into it, run the updater-capable
      `install.bat`, and afterwards: that environment's `microclaw` still
      imports and still reports its own path, the installer **printed** where it
      is and that the icon has moved, and nothing under it was written. Hash its
      `site-packages` before and after.

## 58d — cached status, one staging job, and the banner

**Branch:** `design58/endpoints`. **Design sections:** "Check quietly, ask where
the user already is" (the API and banner halves), "Delivery" step 3.

### Items

- [ ] **1. `GET /api/update` reads cached state only and never performs network
      I/O.** This is the route the browser polls.
- [ ] **2. `POST /api/update/check`** is the explicit "Check now".
- [ ] **3. `POST /api/update/stage`** begins **one** background staging job. A
      second request while one runs is refused, not queued.
- [ ] **4. `POST /api/update/restart`** refuses unless the session is idle —
      no agent turn, acquisition, pending confirmation or setup write in flight.
      (The shutdown and relaunch it requests land in 58e.)
- [ ] **5. `POST /api/update/dismiss`** carries "Later" (seven days for that
      commit) and "Skip this commit".
- [ ] **6. All mutating routes take the same remote authentication** as every
      other mutating route, and the same cross-origin block.
- [ ] **7. The browser never calls GitHub.** It reads local update state only.
- [ ] **8. The banner** goes at the top of the existing UI in `serve.html`,
      alongside `#setup-banner`, `#pair-banner` and `#key-banner`, following their
      markup and CSS rather than introducing a new pattern. Short SHA and commit
      subject, **Update** · **Later** · **View on GitHub**; Update becomes
      progress, then **Restart now** / **Restart later**.
- [ ] **9. When the candidate is not `ready` under 58b's comparison**, the banner
      says the update needs the maintainer and carries the escape sentence.
- [ ] **10. The agent cannot see or reach any of it.** Not a tool, not in
      conversation history, not in the context provider. Identity test.

### Tests

- [ ] `GET /api/update` performs no network I/O — patch the provider and assert
      it is never called, rather than asserting on a timing.
- [ ] A second `stage` while one runs is refused with a distinguishable status.
- [ ] `restart` refuses during a turn, during an acquisition, with a pending
      confirmation, and during a setup write — four cases, not one.
- [ ] Every mutating route rejects an unauthenticated remote request, in the
      style `tests/test_host_isolation.py` already uses.
- [ ] "Later" suppresses the same commit for seven days and **does not** suppress
      a different one; "Skip this commit" suppresses only that commit.
- [ ] The serve page renders the banner from a fixture state, and the transcript
      JS test file's existing harness covers the three button states.
- [ ] `TOOL_REGISTRY`, the tool schemas and the model's context are unchanged.

### Gate — demo machine, `design/58-block58d-demo-gate.md`

Against a **real** discovered commit, not a fixture. Because the repository is
private, the clone provider is the one that produces a candidate; to have
something to discover, the runbook checks out a commit one behind `origin/main`
into the managed slot first, as a literal command.

- [ ] The banner appears with the real short SHA and subject, and matches
      `git log -1 --format=%h %s origin/main` printed beside it.
- [ ] "Later" hides it; a restart of the server does not bring it back.
- [ ] "Check now" re-checks and the attempt timestamp in `update-state.json`
      moves.
- [ ] "Restart now" **refuses** while a turn is running — start a long turn, then
      click it, and capture the refusal.
- [ ] The browser made no request to GitHub — from the browser devtools network
      log, saved.

## 58e — restart, the terminal line, and the end-to-end update

**Branch:** `design58/restart`. **Design sections:** the `MICROCLAW_UPDATE_RESTART`
paragraph and the "Restart now"/"Restart later" paragraph of "Put the updater
outside the environment it replaces", the REPL half of "Check quietly", "Delivery"
step 4.

### Items

- [ ] **1. The exit pause is suppressed for an update restart, and only then.**
      `Microclaw.cmd` sets `MICROCLAW_FROM_SHORTCUT=1`, which makes `main()`
      register the atexit handler that blocks on
      `Press Enter to close this window...` (`microclaw/shortcut.py:168`) — and
      the launcher is waiting on exactly that exit. The **server process sets
      `MICROCLAW_UPDATE_RESTART=1` on itself** while handling
      `/api/update/restart`, immediately before requesting shutdown; the
      already-registered handler checks the flag **when it runs**. The launcher
      cannot pass it at spawn, because nothing knows at spawn whether this run
      ends in an update.
- [ ] **2. The pause stays for every ordinary exit.** It exists so a novice can
      read a startup refusal.
- [ ] **3. "Restart now"** requests a graceful shutdown; after the child exits the
      **still-running launcher** activates the pending slot and starts
      `microclaw serve` again. Closing the console may kill both, so it is never
      relied on for post-exit work.
- [ ] **4. "Restart later"** activates at the beginning of the next desktop
      launch.
- [ ] **5. "Restart now" is offered only when the launch nonce and the
      launcher-owned flag are both present and the pending slot is valid.** A
      user who runs `env-a\Scripts\microclaw.exe serve` directly may stage and
      choose Restart later, and the UI **must not** claim it can relaunch that
      process.
- [ ] **6. The REPL prints one unobtrusive line after startup** and asks
      `Update now? [y/N]` **only when stdin is interactive**. Never under
      redirection, tests, a service, or `--no-update-check`. **No prompt before a
      `serve` launch** — desktop users may not understand the console, and it
      delays the interface they do understand.
- [ ] **7. `--no-update-check` reaches both entry points** — it belongs on the
      top-level parser, beside `--safety-config`, not on the `serve` subparser,
      because subcommand flags there would clobber the session defaults.

### Tests

- [ ] The atexit handler skips the prompt when `MICROCLAW_UPDATE_RESTART=1` is
      set **at handler-run time**, and pauses when it is set at neither time and
      when it is set only at registration time. The middle case is the one that
      matters.
- [ ] `/api/update/restart` sets the flag **before** requesting shutdown —
      ordering asserted by instrumentation.
- [ ] "Restart now" is not offered without nonce + launcher flag + valid pending
      slot; each of the three absences separately.
- [ ] The REPL prompt appears with a fake interactive stdin and is absent under a
      non-tty, under `--no-update-check`, and under `MICROCLAW_UPDATE_CHECK=0`.
- [ ] `serve` never prompts, under any combination.

### Gate — demo machine, `design/58-block58e-demo-gate.md`

The full Delivery-step-4 sequence. **Drive it from the desktop shortcut, not a
terminal**, so the exit pause is live and a stall is visible — a terminal run
cannot fail this gate the way a user's launch can.

- [ ] Clone install → discovered upstream commit → update → **Restart now** →
      the new slot is serving, proven from `microclaw-slot.json` beside the
      running interpreter.
- [ ] The restart did **not** stall on `Press Enter to close this window...`,
      timed.
- [ ] An ordinary Ctrl-C exit **does** still pause.
- [ ] A direct `env-a\Scripts\microclaw.exe serve` stages and offers **Restart
      later only** — the absence of the Restart-now control is the evidence.
- [ ] Active files locked during staging do not break the build.
- [ ] A deliberately failed install, and a deliberately failed start, each leave
      the old slot serving.
- [ ] Offline launch works.
- [ ] PyPI unreachable during staging reports "the update could not be built",
      keeps the active slot, and does not retry inside the interval.
- [ ] Update once with **Micro-Manager closed**: the new slot is kept, with no
      rollback and no relaunch.
- [ ] `%APPDATA%` config, key and histories preserved, hashed before and after.
- [ ] A **public ZIP** install records `public-head`, and its status line says
      automatic updates become available when the repository is public — it does
      not error and does not nag.

## 58a demo gate round 1 — 2026-08-26, FAILED, and what it caught

**Result: failed at limb 1, then printed `BLOCK 58a DEMO GATE PASSED`.** Both
halves of that sentence are findings.

### The defect it found is worth the trip

`clone_provenance` refused the demo machine's clone:

```
UpdateError: clone remote does not match the compiled repository identity
```

The clone's remote still names **`zacsimile/microclaw`**, the pre-transfer
namespace. GitHub redirects Git traffic after a transfer, so it had kept fetching
and nothing had ever surfaced the stale name. The refusal was a coordinator
review finding (S7, round 1) implemented against the **compiled-in** name, and it
therefore fails closed on precisely the case requirement 3 exists to protect:

> **The path must survive the repository moving.** Once an install is updating, a
> rename or an organization transfer must not silently cut it off.

**That machine is not misconfigured — it is the production condition.** Every
GitHub Desktop clone taken before 2026-08-26 still carries the old name, and none
of them has any reason to notice. **Do not repoint the demo machine's remote.**
It is the only fixture available for requirement 3, and a green gate bought by
editing it would ship code that cuts off every existing user on their first check.

**Why four review rounds and a green suite missed it:** every clone fixture uses
a `file:` remote, and the refusal is guarded by
`remote_identity.startswith("github:")`. The entire suite skipped the branch. **A
fixture that cannot reach the code is not coverage of it** — the same shape as
`CLAUDE.md`'s "a fake that encodes your assumption", one step earlier: here the
fake could not even execute the assumption.

Fixed by deleting the compiled-in refusal, keeping `_verify_clone_remote`'s
recorded-versus-current comparison (which is the real threat — a clone repointed
*after* bootstrap), and recording a redirected name as a note. New fixtures use
real `github.com` URLs in both SSH and HTTPS form.

### Three defects were mine, and they are one defect

The runbook was seven copy-paste PowerShell blocks. All three follow from that.

1. **It could not enforce its own sequencing.** `$ErrorActionPreference = 'Stop'`
   was set, but pasted interactively a `throw` ends the current pipeline, not the
   session. Limbs 1–5 failed, each printed its `throw`, and the final block's
   `Write-Host 'BLOCK 58a DEMO GATE PASSED'` ran anyway. **A gate that can print
   PASSED while failing is worse than no gate.**
2. **It called bare `python`.** Rig runbooks use `uv run`; the operator corrected
   every line by hand while running it.
3. **It captured nothing.** No transcript, no artifact directory — the evidence
   reaching the coordinator was console scrollback the operator copied out.

**The rule this establishes: if every step of a gate is a literal command, it is
a program, and it ships as one.** A runbook is for steps a human performs and
judges — driving a session, watching an optic, deciding whether a field looks
right. Limbs that only compute belong in a script that runs them all, reports
each independently, writes its own evidence, and exits nonzero. `design/58-block58a-demo-gate.py`
plus a thin `.ps1` wrapper replaces the prose version; the wrapper is the only
thing the operator runs.

Reporting each limb independently rather than aborting at the first failure is
deliberate: round 1's cascade meant one refusal hid five untested limbs behind
`TypeError: 'NoneType' object does not support item assignment`.

## 58b's gate is expected to report INCOMPLETE, and that is the correct result

`compare_slot_configurations` compares **one config file** classified by **two
code versions**. Two genuinely different validators cannot exist until 58c builds
the second slot, so one limb reports **NOT EXERCISED** naming 58c, the banner
reads INCOMPLETE, and the script exits nonzero. **Do not read that as a failure
and do not engineer it away** — it is the honest state of the evidence, and the
alternative that round 1 attempted was worse.

Round 1's gate resolved its "two slots" as the operator's real installed
Microclaw at `%LOCALAPPDATA%\microclaw\env\Scripts\microclaw.exe` and whatever
`microclaw` was on `PATH`. Neither is the code under review, so the gate could
have failed because the installed copy predated the block, or passed while
exercising something else entirely. It also classified **three different files**
with two executables and then called the **pure** comparison on results it had
gathered itself — re-implementing the mechanism rather than invoking it, so
`compare_slot_configurations`, the only new runtime behaviour in the block, had
no gate coverage at all. Its unit test monkeypatches `subprocess.run`, so no real
process was ever spawned by anything.

The rewrite runs the real function against the checkout's own CLI and a
**gate-written stub** as the candidate — two real subprocesses, the real argument
order through a real argparse, real JSON over a real pipe — and drives all nine
cells plus three failure paths that way. Every limb name and detail string
carries "(candidate = gate stub, not a second slot)" so no later reader mistakes
it for two-slot evidence.

**One thing the runner did better than asked.** The `limb` decorator now takes a
mandatory `fails_if` argument, recorded into every result, so "what would make
this limb fail" is answered structurally rather than by discipline. 58a's
unfalsifiable opt-out limb could not have been written this way. Consider it for
58c's gate.

## Owed evidence that cannot be booked

Recorded rather than inferred, the way design/56 records its Nikon limbs.

- **The public provider has no positive path while the repository is private.**
  Every gate above exercises its 404, its caching and its silence, which is the
  behaviour shipped to real users today; none exercises a successful public
  discovery, download and install. Two ways to close it, both operator
  decisions:
  **(a)** on the day the repository goes public, run 58a's and 58e's public limbs
  against it — this is also requirement 2's own acceptance test and should be run
  then regardless; or
  **(b)** create a small public fixture repository under `Micro-Claw` and gate a
  **throwaway** build whose only diff from `main` is the three compiled-in
  identity constants.
  **Decided 2026-08-26 (operator): (a).** The flip-day run is requirement 2's own
  acceptance test and has to happen then regardless, so (b) would duplicate it
  early at the cost of a public repository to create and later delete. **This is
  therefore a real, accepted gap in the shipped evidence** — 58a and 58e gate the
  provider's 404, caching and silence, and nothing gates a successful public
  install until the day the repository goes public. Write that day's run into the
  flip checklist rather than trusting anyone to remember this paragraph.
  **Do not add an environment override to production code to make this
  testable.** §"Ship the public-head path" forbids a browser-supplied URL for a
  reason, and a test hook in the trust boundary is the same hole with a nicer
  name.
- **Nothing is owed to a microscope.** No limb of design/58 needs M2, M5 or a
  Nikon. 58b's optional fuller-config limb is a convenience, not a debt.

## Post-merge design gate

- [ ] Rewrite §"Decision"'s branch-protection paragraph in the past tense with
      what was actually done, and record the ruleset id or the escape taken.
- [ ] **Amend §"Track the private clone's upstream for now"**: it says the
      bootstrap records the clone's *current branch*. Discovery must track
      `<remote>/main`. See 58a item 6a for why our own gate machine is the case
      that breaks it.
- [ ] **Amend §"Put the updater outside the environment it replaces"** to say
      what happens to a conda, miniforge or embedded-Python install. Today it
      says only that `install.bat` "migrates the existing `env` install", which
      is silent about the layout `shortcut.py` explicitly supports and the lab
      machine actually runs. See 58c items 12a–12c.
- [ ] Reconcile the `update-state.json` and `microclaw-slot.json` field lists in
      this document against what shipped. A design doc that names fields the code
      does not write is how 55b's `hasattr` misreading happened.
- [ ] Fold into `CLAUDE.md` **only** what is generic. Two candidates are already
      visible and neither is Micro-Manager-specific: *only the thing outside both
      slots may write the thing outside both slots*, and *a health marker's
      existence is not health — a nonce-matched, freshly written marker is.*
      Do not fold Windows layout details there; they belong here.
- [ ] Move the `design/35` boundary note off "nothing is assigned" and onto
      design/58's state.
- [ ] Tick the carried-forward register rows this touches, if any. **The
      eleven-undecorated-tools row does not move** — design/58 adds no tool.

## Run ledger

| Block | Depends on | Branch | Start commit | Implementation | Gate | Merged | Design reconciled |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 58-P | — | n/a (repo config) | — | operator decision | n/a | — | — |
| 58a | — | ~~`design58/discovery`~~ | `4103d36` | `84d49cb` → `7c3a71f`; coordinator `9c087e2`, `b2f1e58`, `70650f0`, `1ccb677`; codex, **4 rounds, 13 findings** | **PASS** demo, 2026-08-26, **4 rounds** — 3 failed on gate defects, all 9 limbs on the 4th | `33028e9` 2026-08-26 | done — this section |
| 58b | — | ~~`design58/classification`~~ | `1a582dc` | `a462032` → `f50fd82`; coordinator `30b0d9b`; codex, **2 rounds, 6 findings**, 3 turns killed mid-flight | **PASS** demo 2026-08-27 — 16 PASS / 0 FAIL / 1 NOT EXERCISED; verdict INCOMPLETE **by design**, awaiting 58c | `3baec05` 2026-08-27 | done — this section |
| 58c | 58a, 58b | `design58/two-slots` | — | — | demo — not run | — | — |
| 58d | 58a, 58b | `design58/endpoints` | — | — | demo — not run | — | — |
| 58e | 58c, 58d | `design58/restart` | — | — | demo — not run | — | — |

**Baseline on `main` at `feb0565`, coordinator-measured: 2182 passed / 99
skipped / 3 warnings** (macOS). Windows reads the same collected total with a
different skip split. **Gate on zero failures, never the count.**

## Resuming this block cold

Everything needed is on `main`.

**State as of 2026-08-27. Next block is 58c, and it is the heaviest one.**

### Read these before assigning 58c, in this order

1. `CLAUDE.md` §"The block workflow" — authoritative, and **58a rewrote step 6**.
   Also its §"six contracts" preamble, whose fixture rule 58a added.
2. This file: §"58a demo gate round 1", §"58b's gate is expected to report
   INCOMPLETE", then the `## 58c` checklist section.
3. `design/prompts.md`, the design/58 entries — the 58b one carries the runner
   interruption recovery and the `fails_if` idiom.
4. **The two gate scripts on `main` are the template**:
   `design/58-block58a-demo-gate.py` and `design/58-block58b-demo-gate.py`, each
   with its thin `.ps1`. Do not design 58c's gate from scratch — 58b's `limb`
   decorator takes a **mandatory `fails_if`** argument recorded into every
   result, which is the strongest thing either block produced. Copy it.
5. Code 58c touches: `install.bat`, `microclaw/shortcut.py`, `microclaw/paths.py`,
   `microclaw/webserve.py:serve`, and `microclaw/updates.py`'s slot-marker
   functions (58a shipped `slot_marker_path` / `write_slot_marker` /
   `read_slot_marker` already — 58c consumes them, it does not rewrite them).

### What makes 58c different from 58a and 58b

- **It is the only block that can brick an install.** Its gate replaces the
  managed environment, kills the server and forces rollbacks, repeatedly. The
  runbook must back up `%APPDATA%\microclaw` and the existing
  `%LOCALAPPDATA%\microclaw\env` first, as a literal command that prints the
  copy.
- **Its `updater-launcher.ps1` cannot run on CI at all**, which is the exact
  property that let 58a's defect through four review rounds. **Put the state
  machine in Python where it is unit-testable — activation, pending consumption,
  nonce matching, stale-marker rejection, slot-metadata mismatch, rollback, the
  protocol-too-old refusal — and keep the `.ps1` thin.** Whatever remains only in
  PowerShell is gated, structurally tested, or admitted as untested.
- **It discharges 58b's one debt**: two real slot validators classifying one
  shared config file. That limb is in its checklist.

State:

- **58a is MERGED** (`33028e9`) and its branch, worktree and gate are closed.
  `main` measures **2220 passed / 99 skipped / 3 warnings** (macOS,
  coordinator-measured). Its round history is §"58a demo gate round 1" and
  `design/prompts.md`.
- **58b is MERGED** (`3baec05`, 2026-08-27), branch and worktree deleted. Its
  demo gate ran 16 PASS / 0 FAIL / **1 NOT EXERCISED**, verdict INCOMPLETE — the
  correct result, not a failure. `main` measures **2244 passed / 99 skipped / 3
  warnings** (macOS, coordinator-measured), from 2220.
- **58b left one debt for 58c**, and it is the only one: two real slot
  validators classifying one shared file. 58c creates the second slot, so its
  gate **must** close that limb. It is written into the carried-forward list
  below.
- **Three of five runner turns on 58b were cut short** — one OpenAI usage limit,
  two harness kills, one of which left the gate script deleted mid-rewrite after
  an `apply_patch` refusal on a combined delete-and-create. None was a code
  problem. The recovery that worked: keep the interrupted work, tell the next
  turn in writing not to trust it, and commit any part that is complete and
  correct so a further interruption cannot lose it (`30b0d9b`).
- **`design/58` is not a row in `design/35`.** It tracks itself, here.
- **The repository is already `Micro-Claw/microclaw`, id 1238975695, private**,
  verified against the API on 2026-08-26. No production install will ever have to
  follow the rename redirect; the redirect handling ships anyway, per §"Ship the
  public-head path".
- **58-P is not a code block and cannot be done by a runner.** Branch protection
  is currently *unavailable* — Free org, private repo, HTTP 403 — and there is no
  workflow to require. Read §"The ship prerequisite is not merely undone" before
  planning around it.
- **Every gate is the demo machine.** The Nikon is gone and no limb of this
  design needs a microscope at all.
