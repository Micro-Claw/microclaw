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
tracked upstream may be offered to users, so `main` needs a boundary in front of
it.

**Decided 2026-08-27 (operator): the boundary is the block workflow's own
pre-merge testing, and `main` is the release branch.** Nothing merges without
the full suite passing and a gate run on real hardware — the demo machine, and
M2 or M5 where a block needs them. That is materially *more* than a required CI
check would prove: CI would run the suite on two platforms, while this exercises
the mechanism on the machines it ships to. An earlier draft of this paragraph
called protected `main` a prerequisite to ship and said a release-note
disclaimer could not substitute for it. Correct about disclaimers, wrong about
this: a slower human gate is not a disclaimer, and treating it as one would have
held the whole feature back from the users it exists for.

Branch protection and a required CI check are **additions for the public flip**,
not preconditions for the private preview — they become available on the Free
plan the day the repository goes public. A `develop` branch, if the merge rate
ever justifies one, is the same kind of later addition.

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
  launcher-protocol.txt      the protocol THIS launcher implements
  active-slot.txt            exactly `a` or `b`
  pending-slot.txt           absent, `a` or `b`
  launch-health.txt          one nonce, written by the child, read by the launcher
  rollback-report.txt        present only until the next healthy launch prints it
  launcher.log               bounded; one line per launch, plus rollback reports
  update-state.json          provenance, discovery, dismissal and staging state
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

**What actually ships, reconciled against the code (2026-08-27).**
`microclaw-slot.json` carries exactly `commit` and `required_launcher_protocol`;
`commit` is a full SHA **or the literal `unknown`**, which a ZIP install
legitimately produces and `write_slot_marker` therefore accepts and validates.
`update-state.json` is written by `clone_provenance` / `public_provenance` —
never hand-rolled by the installer, which learned that the hard way — and carries
`provenance`, `repo_id`, `repo`, `canonical_repo`, `branch`, `installed_commit`,
plus for a clone `clone_path`, `upstream`, `remote`, `remote_url`,
`remote_identity`, `clone_repository_note`, `tracked_branch` and
`git_executable`. Checking and staging add `last_attempt`, `next_check`,
`last_error`, `last_success`, `discovery`, `build_error` and
`build_failed_commit`. **`git_executable` and `remote_identity` are load-bearing**:
`_git()` refuses without the first and `_verify_clone_remote` without the second,
which is why an installer that invented its own record broke every fresh clone's
first check.
Nothing in the tree records a commit today and `__version__` is `0.1.0` and
stays there, because a channel that follows head has no version to compare — so
the package does not carry its own identity and the smoke check has no marker to
match. What is verified, before the build rather than after it, is that the
staged source *is* the requested commit: the clone provider archives that exact
fetched SHA, and the public provider downloads an archive pinned to the SHA the
API returned. A ZIP a user downloaded themselves from `Code -> Download ZIP`
has no Git metadata and correctly bootstraps as `unknown`.

**Migration covers `%LOCALAPPDATA%\microclaw\env` and nothing else, and it says
so out loud.** `shortcut.py:launcher()` supports a second layout on purpose — its
comment names it: *"Under conda — what the lab machine runs — python.exe sits at
the env root."* Such an install is not at the migrated path, so `install.bat`
builds a second, uv-managed installation beside it and moves the desktop icon,
leaving the old environment orphaned with old code in it. The installer therefore
**detects and prints** any resolvable `microclaw` outside the managed root —
through `PATH`, through `CONDA_PREFIX`, and through the common conda roots — names
the route it was found by, and states plainly that an arbitrary embedded Python
cannot be discovered automatically. It never imports from it, installs into it,
or modifies it. On the demo machine this fired for two environments by two routes
in one run.

**The updater never installs into an environment it did not create.** Every
target is constructed as `env-a` or `env-b` under `%LOCALAPPDATA%\microclaw`; no
path derived from `sys.executable`, `CONDA_PREFIX`, `PATH` or recorded provenance
can become one. **And an unmanaged install stays silent, not broken**: with no
`update-state.json` it gets no check, no banner and no CLI line, and is left
byte-untouched. That is the shipped behaviour for every conda and
embedded-Python user who never runs the new `install.bat`.

**An interpreter that exists is not an interpreter that runs.** A uv venv's
`python.exe` is a trampoline onto an interpreter recorded in `pyvenv.cfg`, and
when that base goes away the file remains while every spawn fails. `install.bat`
reuses a slot only after `"%MC_PY%" -c "pass"` succeeds, and `--clear` is
reachable only after that probe fails — the one condition where replacing an
environment loses nothing.

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

**What actually ships, reconciled against the code (2026-08-27, block 58d).** All
five routes ship at those exact paths and sit inside `build_app`, so they inherit
the existing bearer/cookie middleware and the cross-origin block with no new
mechanism; the five cases are in
`tests/test_webserve.py::test_every_remote_api_route_accepts_bearer_and_cookie`.
`GET /api/update` returns `managed`, `candidate` (with a server-built `url`),
`last_attempt`, `last_error`, `staging`, `pending_staged`, `comparison_refused`,
`comparison_refusal_reason`, `automatic_restart` and `dismissal` — and nothing
else; a suppressed candidate is reported as `null` rather than filtered in the
browser. `POST /api/update/check` is **rate-limited to three per minute per
client** through the existing `_RateLimiter`, because `force=True` bypasses the
due interval and an unbounded button is a GitHub request per click; the fourth is
429. `POST /api/update/restart` refuses `409` while any of the four idle
conditions holds, `409` again when automatic restart cannot be offered, and
`501 {"restart_requested": false}` on the idle offerable seam that **58e** fills.
Dismissal is a single `{action, commit, until}` record — one candidate is current
at a time, so a per-commit map would be state nobody reads. The banner copy is as
written above, with *"Building the update…"*, *"The update is ready to restart."*
and *"This update needs the maintainer. "* plus the escape sentence
`compare_config_classifications` already writes. **Skip this commit and Check now
ship as routes with no UI**: the settings surface this paragraph offers is not
built, and 58d's gate drives Check now over HTTP, which is honest because there
is no button to press. The pure `state -> view` function is
`Transcript.updateBannerView` in `transcript.js`, beside the existing shared
`initTheme`, so the node harness covers the three button states; the DOM wiring
stays inline in `serve.html` and assigns the commit subject with `textContent`,
because a subject is remote text. The page polls at 30 s, and at 2 s only while
`staging` is true.

**One caller the checklist never named.** `check_for_update` had no production
caller at all, so nothing populated the cache the banner reads. `serve()` now
starts one due check on a daemon thread before `build_session`, guarded by
`checks_enabled`, so it never delays the interface and `--no-update-check` skips
it outright. The rig proved it: `Prepare` deletes `last_attempt`, and a timestamp
was present before any manual check.

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

**Nothing outside the code blocks the first shipped installer.** The repository
has already moved, and the pre-merge boundary is the block workflow's own
testing — see §"Track the private clone's upstream for now". Immutable
repository-ID verification covers a later rename or transfer.

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

### `main` is the release branch, and nothing gates the first installer

**Decided 2026-08-27 (operator).** Users pull from `main`; the boundary in front
of it is the block workflow's own pre-merge testing on multiple machines. See
§"Track the private clone's upstream for now" for the reasoning and what changes
at the public flip.

For the record, since it will come up again: branch protection and rulesets are
**unavailable on this repository today** —

```
GET /repos/Micro-Claw/microclaw/rulesets            -> 403
GET /repos/Micro-Claw/microclaw/branches/main/protection -> 403
   "Upgrade to GitHub Pro or make this repository public to enable this feature."
```

`Micro-Claw` is a Free organization and the repository is private. Both become
available the day it goes public, which is when they get added. **That
unavailability blocks nothing**, because it was never the boundary — it is an
extra mechanical check to layer on later.

**And CI would build nothing.** The updater stages an exact commit and runs
`uv pip install` **on the user's machine**; no artifact is produced centrally
and Microclaw is not distributed through PyPI. A CI workflow's only possible
role here is as a test gate, which is the role the pre-merge process already
fills.

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

## 58-P — branch protection and CI, at the public flip

**Not a code block, not assignable to a runner, and — as of 2026-08-27 — not a
blocker.** It was written as a prerequisite to shipping the first installer;
that framing was wrong and is corrected above. These are additions to make on
the day the repository goes public, when they first become possible.

- [ ] On the flip: add `.github/workflows/tests.yml` — Linux and Windows, not
      macOS (runner multipliers are 1x / 2x / 10x, and Windows is the only
      platform this ships on). Public repositories get Actions free and
      unlimited, so the private-repo minute budget stops mattering.
- [ ] On the flip: protect `main` and require that check. **Note that a required
      status check enforces only on pull requests**, so turning it on converts
      every block merge into a PR — a real change to `CLAUDE.md` step 9 and to
      the standing no-PR preference. Decide that deliberately at the time, not
      by reflex.
- [ ] A `develop` branch if the merge rate ever justifies one. Not now.

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

### Gate — demo machine, a runbook **and** a program

**Decided at assignment, 2026-08-27.** 58a's rule is that limbs which only
compute ship as a program; 58c is the first gate of this design that genuinely
has both kinds of limb. It therefore ships as **three** files on the branch:

- `design/58-block58c-demo-gate.md` — the human-driven sequence only: back up,
  run `install.bat`, double-click the desktop icon, close Micro-Manager, break a
  slot, launch again. Each step names the *mechanism* under test, not the
  outcome, and says what to record.
- `design/58-block58c-demo-gate.py` + `.ps1` — every limb that only computes:
  layout and marker inspection, the process command line, hash comparison of
  `%APPDATA%`, the two-slot classification (58b's owed limb), the non-uv
  environment's untouched `site-packages`. Copy 58b's structure, **including the
  mandatory `fails_if` argument of the `limb` decorator**, PASS / FAIL / NOT
  EXERCISED, independent limbs, its own log, nonzero exit.

The program is run **after** the human steps and reads the state they left
behind, so a human step that was skipped shows up as a failing or NOT EXERCISED
limb rather than as silence. No `<placeholder>` in either file.

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

- [x] **1. `GET /api/update` reads cached state only and never performs network
      I/O.** This is the route the browser polls.
- [x] **2. `POST /api/update/check`** is the explicit "Check now".
- [x] **3. `POST /api/update/stage`** begins **one** background staging job. A
      second request while one runs is refused, not queued.
- [x] **4. `POST /api/update/restart`** refuses unless the session is idle —
      no agent turn, acquisition, pending confirmation or setup write in flight.
      (The shutdown and relaunch it requests land in 58e.)
- [x] **5. `POST /api/update/dismiss`** carries "Later" (seven days for that
      commit) and "Skip this commit".
- [x] **6. All mutating routes take the same remote authentication** as every
      other mutating route, and the same cross-origin block.
- [x] **7. The browser never calls GitHub.** It reads local update state only.
- [x] **8. The banner** goes at the top of the existing UI in `serve.html`,
      alongside `#setup-banner`, `#pair-banner` and `#key-banner`, following their
      markup and CSS rather than introducing a new pattern. Short SHA and commit
      subject, **Update** · **Later** · **View on GitHub**; Update becomes
      progress, then **Restart now** / **Restart later**.
- [x] **9. When the candidate is not `ready` under 58b's comparison**, the banner
      says the update needs the maintainer and carries the escape sentence.
- [x] **10. The agent cannot see or reach any of it.** Not a tool, not in
      conversation history, not in the context provider. Identity test.

### Tests

- [x] `GET /api/update` performs no network I/O — patch the provider and assert
      it is never called, rather than asserting on a timing.
- [x] A second `stage` while one runs is refused with a distinguishable status.
- [x] `restart` refuses during a turn, during an acquisition, with a pending
      confirmation, and during a setup write — four cases, not one.
- [x] Every mutating route rejects an unauthenticated remote request, in the
      parameterized table
      `tests/test_webserve.py::test_every_remote_api_route_accepts_bearer_and_cookie`.
      (This row said `tests/test_host_isolation.py`, which is about the suite not
      reading its host; corrected at the 58d design gate.)
- [x] "Later" suppresses the same commit for seven days and **does not** suppress
      a different one; "Skip this commit" suppresses only that commit.
- [x] The serve page renders the banner from a fixture state, and the transcript
      JS test file's existing harness covers the three button states.
- [x] `TOOL_REGISTRY`, the tool schemas and the model's context are unchanged.

### Gate — demo machine, `design/58-block58d-demo-gate.md`

Against a **real** discovered commit, not a fixture. Because the repository is
private, the clone provider is the one that produces a candidate.

**This paragraph originally said to install a commit one behind `origin/main`
into the managed slot, and that cannot work** — the slot is the code under test,
so it must carry the block. The arrangement that ships: install from the block
branch, then `Prepare` sets the recorded `installed_commit` to `origin/main~1`, a
real earlier commit. An unmerged branch is not an ancestor of `origin/main`, so
`discover_clone` correctly reports `diverged` and offers nothing; the rewrite
gives discovery something genuine to find while leaving the slot's code alone.
It is real production state, so `Prepare` backs it up, a `Restore` phase puts it
back, and a scored limb fails if it did not — CLAUDE.md's rule about a gate
leaving production state altered. **58e inherits this problem** and will need the
same arrangement, or a merge first.

- [x] The banner appears with the real short SHA and subject, and matches
      `git log -1 --format=%h %s origin/main` printed beside it.
- [x] "Later" hides it; a restart of the server does not bring it back.
- [x] "Check now" re-checks and the attempt timestamp in `update-state.json`
      moves. **Driven over HTTP, not from a button** — Check now ships as a route
      with no UI, so there is nothing to click; the gate says so out loud.
- [x] "Restart now" **refuses** while a turn is running — a real long turn ran
      while the gate posted `/api/update/restart`, and the 409 named the turn.
      **The route, not the button**: the button appears only once a slot is
      staged, and 58d stages nothing on the rig. The *button* limb reports NOT
      EXERCISED and is 58e's — see §"58d's gate reports INCOMPLETE by design".
- [x] The browser made no request to GitHub — from the browser devtools network
      log, saved. Seven requests, all to `127.0.0.1:8000`. **Score the requests,
      never a grep of the file**: a HAR exported "with content" carries response
      bodies, and `/api/update`'s body contains the View-on-GitHub link the
      server builds, which round 1 read as a call to GitHub.

**No rig evidence in this block for anything behind staging**: items 3 and 9,
and the progress and restart banner states of item 8, are implemented and
unit-tested but were never seen on the demo machine, because staging publishes a
pending slot the next launch would act on. They are 58e's to gate.

## 58e — restart, the terminal line, and the end-to-end update

**Three coordinator additions, decided at assignment 2026-08-27**, recorded here
the way 58d's two were, so the post-merge reconciliation has something to check
them against. Each was found by reading the code on `main`, not by the runner.

1. **Nothing reconciles `installed_commit` after an activation.** Grep is
   conclusive: `stage_inactive_slot` writes the *slot marker*, and no code path
   writes `installed_commit` into `update-state.json` after `install.bat`. So a
   machine that completes an update keeps discovering, offering and rebuilding
   the same commit forever, and the end-to-end gate limb below would show the
   banner still offering the commit it just installed. The reconciliation
   belongs in `activate_pending` and `rollback_slot` — the two functions that
   change which slot is active — reading the newly active slot's own marker.
   **Not at launcher-health time**, tempting as that is: 58d's gate arrangement
   rewrites `installed_commit` and depends on that rewrite surviving an ordinary
   launch, and 58e inherits the arrangement.
2. **`serve()` calls `uvicorn.run(app, …)` and keeps no server object**, so
   there is nothing for `/api/update/restart` to ask to shut down. It becomes a
   `uvicorn.Server` whose handle the route can reach; a run with no handle
   refuses rather than claiming a restart it cannot perform.
3. **The launcher needs an explicit restart request, not a pending slot.**
   "Restart later" also leaves `pending-slot.txt`, so the presence of a pending
   slot cannot mean "relaunch me" — an ordinary exit would relaunch forever. The
   request is its own launcher-file, carrying the launch nonce of the child that
   wrote it, consumed once; a stale one is deleted by `fresh_launch` alongside
   the health marker, for the reason 58c already knows — *a marker's existence
   is not health*.

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

## 58c demo gate round 1 — 2026-08-27, FAILED at limb 1, and it was the product

**The installer could not perform the migration it exists to perform.**
`:migrate_layout` moves `%LOCALAPPDATA%\microclaw\env` to `env-a`; `:make_env`
then asked `uv venv` to create `env-a`, which refuses an existing environment —
*"A virtual environment already exists"*. So the **first updater-capable
`install.bat` failed on exactly the machine it was written for**, an existing
install being migrated, and every later phase failed behind it: no launcher
files were written, the desktop icon still named the moved-away `env`, and the
icon was dead until the operator restored.

Two things make this worth writing down beyond the one-line fix (reuse the slot;
`uv pip install --python` upgrades in place; `--clear` only where there is no
interpreter left to lose).

- **Every test in the block passed while this was true.** `install.bat` has 26
  structural tests and not one of them reads `:make_env` in the light of what
  `:migrate_layout` now leaves behind. The two subroutines were each correct and
  their *composition* was not, which is a shape no amount of grepping one
  subroutine finds. When a block adds a step **before** an existing one, test the
  state the new step hands over, not just the new step.
- **A gate whose first limb is the product's own first action is worth more than
  its remaining limbs put together.** Nothing downstream ran, and that cost
  nothing: the failure was in the first ninety seconds and it was unambiguous.

**What the round did establish**, scored from the artifacts rather than the
verdict:

- the five phases each failed **independently** and none printed a pass — 58a's
  cascade defect did not recur, and `results.json` separates FAIL from NOT
  EXERCISED;
- item 12a's notice fired on **two** environments by **two** different routes:
  the operator's own `D:\Code\microclaw\.venv` through `PATH`, and the gate's
  fixture through `CONDA_PREFIX`. That is the item's real evidence and it
  survived the round;
- the coordinator's pre-flight refusal ran and passed (`machine config
  classification: ready`), so the gate did not hang on `install.bat`'s setup
  server;
- the one PASS — APPDATA unchanged — **proves little this round**, because the
  installer never reached the code that writes anything. Recorded as weak, not as
  evidence.

**One gate defect, and it is the fixture rule again.** The non-uv limb reported
the fixture importing `D:\Code\microclaw\microclaw` and called that a changed
environment. `python -c` puts the working directory first on `sys.path` and the
gate runs from the checkout, so the *interpreter* was the fixture's and the
*package* was ours. Every `python -c` that imports microclaw now runs with `-I`
from a cwd outside the checkout.

**A second product defect, found by the restore rather than the gate.** The
restore command ended on *"uv trampoline failed to spawn Python child process"*.
A uv venv's `python.exe` is a trampoline onto an interpreter recorded in
`pyvenv.cfg`, and the demo machine's read:

```
home = D:\Code\microclaw\block5b-20260804-124610\fresh-appdata\uv\python\cpython-3.12-windows-x86_64-none
```

**Block 5b's gate (2026-08-04) redirected uv's Python install directory into a
throwaway evidence fixture.** The venv wrote that path down permanently, and the
demo machine's installed Microclaw broke three weeks later when the fixture was
deleted. uv's own store was never involved and still holds 3.12.13.

Two things follow, and the second is the more valuable.

- `:make_env` reused the slot on `if exist "%MC_PY%"`, so it would have carried
  that fault into `uv pip install` and failed the install a second time for a
  second reason. **The test is execution, not existence** — `"%MC_PY%" -c "pass"`
  — and `--clear` is reached only after that probe fails, the one condition where
  replacing an environment loses nothing. This is `CLAUDE.md`'s "a device that is
  not busy is not a device that arrived", one layer up.
- **A gate must not leave production state pointing into its own evidence
  folder.** Nothing in this repository would have caught that, and it had been
  true and invisible for three weeks. `Verify` now checks the property directly:
  every slot's interpreter must start, its `sys.base_prefix` must exist, and it
  must not sit under the evidence directory. **Candidate for `CLAUDE.md` at the
  post-merge design gate** — it is generic gate-writing guidance with nothing
  Micro-Manager-specific about it.

**And the block that can brick an install shipped without a way back.** The
backup existed; the instruction to use it did not. The runbook now carries a
literal restore command, because "expected during a failed gate and fully
reversible" is only true if the operator is told how.

## 58c demo gate rounds 2 and 3 — 2026-08-27, PASS

**Round 2 passed all ten limbs at `e9bcb20`.** Scored from the artifacts:

- the migration genuinely happened — `legacy_env_before_first: true`,
  `slot_a_before_first: false`, `legacy_env_after_first: false`;
- **all three `:make_env` branches ran on the rig in one Prepare**: rebuild (the
  demo machine's dead interpreter), reuse (the second install), create (`env-b`).
  Code written that morning with no rig evidence got all three paths in one trip;
- the desktop child is `env-a\Scripts\microclaw.exe serve`, proven from
  `Win32_Process`, and its health marker carried the launcher's own nonce;
- the rollback sequence reads correctly in `launcher.log`: slot=b launch at
  11:29:17 reaching no health, slot=a at 11:29:24, `rollback-reported=` at
  11:29:26 — **on the successful launch, not the failing one**. Seven seconds
  between them means the child *exited*, so the `child-exited` branch has rig
  evidence and the 30-second `timeout` branch still has only unit coverage;
- **58b's carried-forward debt is discharged**: `{"active": "ready",
  "candidate": "ready"}` from two real slot CLIs on one config file.

**A passing gate is a place to look for defects, and one limb proved less than
its design item claims.** The Micro-Manager-closed limb checked one added nonce,
an unchanged active slot, and no rollback report — but with no pending slot the
launcher has nothing to roll back to, so it writes no report *whether or not
health was reached*. "No rollback" was not evidence of health. Item 7 (a closed
bridge is never evidence that new code is defective) and the first half of item 8
(the marker **is** written) had no rig evidence at all, and the artifacts could
not distinguish the two outcomes after the fact.

**Round 3 closed it.** `observe_closed` now captures `launch-health.txt` after
the child has gone, and the demo machine returned:

```
"launch_nonce":              "9c8b5d2abcf23c6d81bc5a3418864ea7"
"health_after_child_exited": "9c8b5d2abcf23c6d81bc5a3418864ea7"
```

**The evidence is split across two directories on purpose.** Round 3 ran
Prepare/Closed/Verify only, so its `Healthy` and `Rollback` limbs have no
artifacts there. That is sound because **no product code changed between them** —
`git diff e9bcb20..HEAD -- microclaw/ install.bat scripts/` is empty, and every
commit in between is gate-side. Round 3 independently re-confirmed six of the
other limbs. Do not read round 3's `FAILED` banner as a product failure; read the
two directories together.

### Three gate defects, no new product defects

The ratio has held since 58a, and each of these is generic.

1. **`Verify` compared the slot marker against a live `git rev-parse HEAD`.** The
   marker records the commit the installer *built from*, so an ordinary `git pull`
   between phases read as a marker defect and cost a whole gate rerun. It is
   scored against the commit `Prepare` recorded, and prints a note when HEAD has
   moved.
2. **`Verify` needed all four preceding phases and said so nowhere**, surfacing
   them one failing limb at a time in whatever order the limbs run. Every phase
   records itself in `phases.json`; `Verify` preflights with the exact commands
   still owed, once, and says that a phase already passed in an earlier directory
   at the same product commit does not need rerunning.
3. **Missing evidence reported `FAIL`.** A limb whose phase was never run has not
   failed its mechanism — nothing ran it. They use `need()` now. `need()` did not
   exist in the rewrite, so adding the calls without it would have shipped a
   `NameError`; **replaying the real round-3 evidence through the scorer is what
   caught that**, and replaying returned artifacts through a changed scorer is
   worth doing every time.

## 58d's gate reports INCOMPLETE by design, like 58b's

Written before the trip, so the verdict is not misread — the same shape as
§"58b's gate is expected to report INCOMPLETE".

**One limb cannot run in 58d: the Restart now *button*.** It appears only when a
pending slot is staged, and 58d deliberately stages nothing on the demo machine —
staging publishes `pending-slot.txt`, which the *next* desktop launch acts on, so
a 58d gate that staged would leave the machine set to activate a slot built from
`origin/main`, which does not contain 58d. That is 58e's gate, which owns
activation and rollback. The limb therefore reports **NOT EXERCISED**, which is
never a pass, and the banner reads INCOMPLETE. The route's refusal *is* gated: a
real long turn runs while the program posts `/api/update/restart` and the 409 is
scored. Read the limb list, not the banner.

Two consequences worth recording. The **config comparison has no rig evidence
from a real staging run** in this block; the gate closes as much of it as it can
without staging, by classifying the shared config through the two real slot CLIs
at exactly the paths `stage_inactive_slot` constructs. And **`/api/update/stage`'s
one-job refusal is unit-tested only** — a second concurrent request needs a first
job that really builds.

**The gate rewrites one field of production state, and puts it back.** The block
branch is unmerged, so a real clone install records a commit that is not an
ancestor of `origin/main`; `discover_clone` correctly reports `diverged`, and
there is no candidate to show. `Prepare` therefore sets `installed_commit` to
`origin/main~1` — a real earlier commit, not a fixture — so discovery has
something genuine to find, and leaves the slot's code alone. It backs up
`%APPDATA%\microclaw` and the original `update-state.json` first, a `Restore`
phase puts the field back, and a scored limb fails if it did not. CLAUDE.md's
rule that a gate must not leave production state pointing into its own evidence
folder is what that limb is for.

**Two coordinator additions to the block, for the post-merge reconciliation.**
The ten checklist items do not name a caller for the background check, and
`check_for_update` had none — without one the cache the banner reads is never
populated and the gate's own "banner appears, *then* Check now moves the
timestamp" step cannot run, so `serve()` now starts one due check off the startup
path. And the checklist's auth test points at `tests/test_host_isolation.py`,
which is about the suite not reading its host; the real pattern is
`tests/test_webserve.py::test_every_remote_api_route_accepts_bearer_and_cookie`,
where the five routes now sit. Fix that sentence at the design gate.

## 58d demo gate round 1 — 2026-08-27, PASS: 12 limbs, two gate defects, no product defects

**Verdict INCOMPLETE, which is the expected result** (§"58d's gate reports
INCOMPLETE by design"). Scored from the artifacts in
`block58d-20260827-140049`: **12 PASS / 0 FAIL / 1 NOT EXERCISED**, the last
being the Restart now *button*, which 58e owns.

The machine's own run reported one FAIL and one NOT EXERCISED. **Both were
defects in my gate, not the product**, and both were re-scored by replaying the
operator's returned artifacts through the corrected scorer. `git diff 3e7ce51 --
microclaw/ install.bat scripts/ tests/` is **empty**, so the replay scores the
same product commit the slot was built from; only the scorer changed.

What the artifacts corroborate, cross-checked rather than taken from a verdict:

- the banner text the operator copied out — `05b2a89 — Merge coord/design58d-pushed:
  58d ledger row updated for the gate` — matches `prepare.json`'s `origin_main`
  and the `/api/update` payload's candidate **character for character**, from
  three independent sources. No screenshot was taken and none is needed;
- `last_attempt` held at `1787832075.0012193` across five GETs, to thirteen
  decimal places, with the discovery record byte-identical either side. **The
  cached route performs no network I/O**;
- **the background check the checklist never named a caller for has rig
  evidence.** `Prepare` deletes `last_attempt` and `next_check`, so a timestamp
  present *before* the first Check now can only have been written by `serve()`'s
  startup thread — it was, with `discovery.status == "candidate"`, and the banner
  was on screen before any manual check. A limb was added afterwards to score
  that, because the evidence was in the artifacts with nothing asserting on it;
- "Later" recorded `until` **604858 s** after the previous timestamp — seven days
  (604800 s) plus the 58 s the operator took to click. Suppression is measured
  from the click, and the candidate stayed `null` across a real server restart
  whose own startup check correctly did not re-run;
- the mid-turn refusal named the right mechanism: `409 An agent turn is in
  progress.` while `GET /api/confirm` returned `200 {"grants": []}`, so no stray
  pending confirmation could have produced it;
- **F1's fix has rig evidence without staging**: two real slot CLIs at
  `env-a\Scripts\microclaw.exe` and `env-b\Scripts\microclaw.exe` — the exact
  paths `stage_inactive_slot` constructs — both classified the shared config
  `ready`.

### Two gate defects, and the ratio holds again

1. **The GitHub limb grepped the whole HAR, including response bodies.** Every
   one of the seven requests went to `127.0.0.1:8000` and none to any GitHub
   host; `github.com` appeared only inside `/api/update`'s response body, as the
   View-on-GitHub link the server builds — which is exactly what item 7
   requires. A HAR exported "with content" carries bodies, so a substring grep
   reads the product's correct behaviour as a violation. **Score the requests,
   never the file.**
2. **The idle-restart control assumed any 409 meant "not idle".** The session
   *was* idle: the route passed all four idle checks and refused on offerability
   — `Automatic restart is not available; restart later.` The limb reported NOT
   EXERCISED with a *misleading reason*, and its intended 501 seam is unreachable
   in 58d for the same reason the button is. Rewritten to gate the offerability
   refusal, which is a real design requirement — the UI must not claim it can
   relaunch a process it does not own — and a control that actually fires. The
   501 seam moves to 58e. **A control that cannot fire is not a control**, and
   this one had been written to expect the wrong branch of its own route.

## 58e is pushed for its gate — 2026-08-27

Branch `design58/restart` at `aabbe4e`, implementation pinned at `43db9f0`.
Suite **2322 passed / 99 skipped / 3 warnings** (macOS, coordinator-measured),
from 2296. Three Codex rounds, thirty-two findings, and the pattern of this
design held again: **the product needed one round, the gate needed three.**

**Round 1 shipped a suite that did not run.** The runner's interpreter died with
signal 139, so it reported "static compilation, smoke probes and diff checks
passed" — and the change from `uvicorn.run(app, …)` to `uvicorn.Server.run()`
left four tests that monkeypatch `uvicorn.run` binding a real port and blocking
forever, plus three failing on `app.state` where `build_app` is stubbed as
`object()`. **A runner that cannot run the suite has produced a handoff, not
evidence**, and this is the clearest instance in the design.

**The block's own coordinator addition nearly bricked the thing it protects.**
`_reconcile_installed_commit` raised, and it ran *after* `active-slot.txt` was
rewritten and pending unlinked. Reproduced off-rig: activation raised with the
selector already moved to `b`, nothing left to retry, and the launcher then
refusing to launch at all; in `rollback_slot` it raised before the report was
written, so a rollback became silent. A plain `OSError` from `write_state` — a
locked or read-only file — reaches the same place with a perfectly valid slot.
**Bookkeeping added to a state machine must not be able to fail the state
machine.**

**Five of the gate's limbs could not fail**, which is the third distinct time
this design has shipped one. The direct-executable limb asserted
`automatic_restart is False` at a point where nothing had staged; the
Ctrl-C limb asserted that the operator's own reaction time was positive; the
offline limb scored the Micro-Manager-closed artifact; the locked-files limb
restated the one-job limb. And two limbs **contradicted each other**:
`stage_inactive_slot` either publishes a pending slot or refuses, never both, so
a not-ready limb and a progress limb reading the same `stage.json` could not
both pass. Ask of every limb not only *what would this look like if the
mechanism had not run*, but *can this limb and its neighbour both be true at
once*.

**A limb that asserts a compound expression reports nothing when it fails.**
Every limb in round 2 was `assert a and b and c`; the decorator stores
`str(exc)`, which is empty for a bare assert, so a rig FAIL would have come back
as `FAIL: Restart now button — ` with no numbers. That is the difference between
one trip and two.

**Interruption seven, and the second of its exact kind.** A revision turn was
killed early, after `apply_patch` refused a patch with *multiple operations
targeting one path* — the same refusal that cost 58b its gate script. It had
committed nothing and had deleted `58-block58e-demo-gate.py`, so it was
discarded, the file restored from `3f328d9`, and the next turn told in writing
that nothing survived and that both files must be rewritten **in place**.

Three coordinator corrections went in directly (`aabbe4e`): the not-ready limb
now names its gate stub, per 58b's rule; the offline phase no longer rewrites
`provenance`, because changing the update channel on a machine this gate can
brick buys nothing and offline `discover_clone` correctly records a *warning*
rather than an error, so the limb would have failed the product for behaving as
designed; and the post-Direct step removes the pending selector instead of
deleting `env-b` before a copy that could fail.

## 58e demo gate round 1 — 2026-08-27, STOPPED, and it found the defect that mattered

The operator stopped at the Restart phase: no Restart control appeared in the
browser. **The browser was right** — nothing had been staged — and the artifacts
in `block58e-20260827-155533` name one root cause with a three-phase cascade.

**`uv venv` refuses an existing environment, and staging called it
unconditionally.** `stage_inactive_slot` runs
`uv venv --python 3.12 <inactive slot>`; on uv 0.11.28 that exits 2 with *"A
virtual environment already exists at"*, and after a machine's first update the
inactive slot always is one. **The first update on a fresh install works and
every later one fails.** Confirmed on the demo machine with a throwaway probe:
first `uv venv` exit 0, second exit 2 with that message, `--clear` exit 0.

The diagnosis came from hashes rather than from the verdict. `env-b`'s
executable and marker are **byte-identical across all seven snapshots**, from
`prepare` through `stage.after`; a successful `uv venv` would have wiped that
executable, so the failure is provably the first of the two commands and not the
`uv pip install` that everyone would guess.

**This is 58c's defect one level up.** `install.bat`'s `:make_env` was fixed to
probe before reusing a slot; nothing gave `stage_inactive_slot` the same
treatment, and unlike the installer, staging must *replace* rather than reuse —
it is rebuilding the slot at a different commit. The fix is `--clear`, which is
safe for the reason the two-slot design already guarantees: the rollback target
is always the **active** slot, which staging never touches.

**The fake was the reason the suite could not see it.** Every fake `uv` in
`tests/test_updates.py` returned 0 for `venv` unconditionally, and each one
created the slot directory itself. CLAUDE.md's rule — *when a defect comes back
from a rig, fix the fake before the code* — was applied literally: one shared
`fake_uv` now refuses an existing environment exactly as uv 0.11.28 does, and
`test_staging_replaces_an_inactive_slot_that_already_exists` stages twice in a
row. On the pre-fix tree it fails with `UpdateError: the update could not be
built`, which is the rig failure reproduced off-rig.

**Two more defects the trip exposed, both about being able to diagnose the next
one.**

- **The failure path discarded `uv`'s stderr**, recording only the user-facing
  `"the update could not be built"`. That sentence is right for the banner and
  useless on a rig: it cost this block a second round-trip to learn which
  command had failed. `update-state.json` now also carries `build_error_detail`
  — failing subcommand, exit code, bounded stderr tail — and the gate's
  unreachable-PyPI limb asserts it, so the diagnostic is itself gated.
- **One failed build silently short-circuited every later staging phase.**
  `stage_inactive_slot` refuses immediately while `build_failed_commit` matches
  the candidate inside the interval — correct product behaviour, and it meant
  `NotReady` recorded `"the update could not be built"` instead of a comparison
  refusal, and `Stage` never built at all. Four limbs' evidence lost to one
  failure. Every staging phase now clears that cache first and records what it
  cleared, so a cascade cannot hide a mechanism that was never run.

**What the round did prove, at real production paths.** The one-job refusal is
genuine: `202 {"staging": true}` then `409 An update staging job is already
running` while `staging` was true. And the two-slot comparison ran for real —
`env-a` and `env-b`'s own CLIs both classified the shared config `ready` with
`proceed: true`, each isolated import resolving to its own slot's
`site-packages`. Discovery was correct throughout: the candidate was
`origin/main` by SHA and subject.

**A PowerShell trap worth writing down.** The diagnostic probe first came back as
`NativeCommandError`, not a uv failure: `uv` writes progress to **stderr**, and
`2>&1` on a native command under `$ErrorActionPreference = 'Stop'` — which the
gate's own `.ps1` sets — turns the first stderr line into a terminating error, so
the exit code is never printed. Redirect inside `cmd` when you need both a native
command's stderr and its exit status.

## 58e demo gate round 2 — 2026-08-27, STOPPED, and the spike that should have come first

Round 2 failed the same way round 1 did — no Restart control — and the operator
stopped the gate and asked for **a small spike instead of another full trip**.
That was the right call, and the spike found in one minute what two gate rounds
had not.

**The defect: a slot's CLI hangs forever when it inherits both
`MICROCLAW_FROM_SHORTCUT=1` and a console stdin.** Measured on the demo machine,
identically on both slots:

| invocation of `check-config --json` | result |
| --- | --- |
| stdin = NUL | 0.66 s |
| stdin inherited | 0.67 s |
| `MICROCLAW_FROM_SHORTCUT=1`, stdin = NUL | 0.66 s |
| `MICROCLAW_FROM_SHORTCUT=1`, **stdin inherited** | **timed out at 45 s** |

`pause_on_exit` registers an atexit handler that blocks on
`Press Enter to close this window...`. `Microclaw.cmd` sets that variable,
`updater-launcher.ps1` inherits it into the server, and
`subprocess.run(capture_output=True)` redirects **stdout and stderr only** — so
the classifier child `compare_slot_configurations` spawns keeps the server's
console. It printed its JSON and then blocked forever, and
`classify_config_with_slot`'s 30-second timeout turned that into
*"slot could not classify the safety config"*. **Staging could never complete on
a desktop-launched server**, which is the whole point of the block. Two fixes,
because two things were wrong: the call passes `stdin=subprocess.DEVNULL`, and
`main()` does not register the exit pause for `check-config --json` at all — a
mode whose entire output is JSON must never end by waiting for a keypress.

**This is the fifth time in design/58 that a guard or a path was never executed
by any fixture**, and the first where the unreachable thing was *the environment*
rather than the code: nothing in 2 300 tests spawns a subprocess that inherits a
console, and nothing can, because a test runner has no console to inherit. The
test asserts the argument rather than the behaviour and says so in its docstring.

**A theory of mine that the spike disproved, recorded because I acted on it.**
Round 2's staging died with `[WinError 5] Access is denied` on `write_state`'s
`os.replace`, and I attributed it to a concurrent reader holding the target open
— the browser polls `GET /api/update` every 2 s while staging. The spike ran 300
replaces against **1,071,721 concurrent reads and lost none of them**. The
retry added for that reason is sound hardening and stays, but **it did not fix
what it was written for, and that `WinError 5` remains unexplained.** What the
probe did show is 581 transient *read* failures, which matters more than it
looks: `activate_pending` treats a read failure as "no valid pending slot" and
**discards the staged update**, so one unlucky launch would silently throw away
an update the user had asked to install. `load_state` retries now.

**The gate itself was costing the operator more than the evidence was worth.**
`Prepare` copied both slot environments — hundreds of megabytes — into a
`Documents` folder that is redirected to a network share, on every attempt. The
environments are the one part of this layout `install.bat` rebuilds, and the
runbook's last step reinstalls anyway. It now copies only `%APPDATA%\microclaw`
and the launcher root's own small files, to a local path. **A gate step whose
cost is paid on every retry has to be cheap, because retries are the normal
case.**

**What round 2 did prove.** The NotReady limb passed on real production code —
`stage_inactive_slot` refused with *"The update would downgrade a reviewed config
from `ready` to `blocked`"* through the real comparison, with `env-b` restored
byte-identically afterwards. That mechanism had never run on hardware, and it is
one of the five 58d handed forward.

**The process lesson, and it is the block's most expensive one.** Two rig trips
went to a defect a one-minute, state-free probe found immediately. The gate is
built to *score* a working mechanism; it is a poor instrument for finding out why
one does not work, because every phase drags a full Prepare, an install and a
staging build behind it. **When a gate fails twice for reasons its own artifacts
cannot explain, stop running the gate and write the probe** —
`design/58-block58e-spike.py` is that probe, it needs no Prepare, no installer
and no staging, and the runbook now names it as the first thing to run after any
failure.

## Spike round 4 — 2026-08-27: staging completes, and the exit pause is suppressed

Four spike rounds replaced what would otherwise have been four gate trips. The
last one is the first evidence anywhere that the block's central mechanism works.

**Full staging, end to end, on the demo machine.** The real `stage_inactive_slot`
ran against a scratch root — `git archive` 0.45 s, `uv venv --clear` plus
`uv pip install` **12.67 s**, slot marker written with the right commit, pending
published, and `compare_slot_configurations` returning `proceed: true` with both
sides `ready`. Nothing real was written: the scratch root held a **junction** to
the live `env-a`, so the comparison's active side was the operator's own CLI
exactly as production has it, and `live_env_a_intact` was true afterwards.
Rounds 1 and 2 both died inside this function without ever completing it.

**The exit pause is suppressed, and the control fired.** `MICROCLAW_UPDATE_RESTART=1`
returned in 0.66 s; the control without it **timed out at 45 s**. A suppression
probe whose control also passes has measured nothing — 58a shipped a limb like
that — so the spike refuses to report a result when its control does not hang.

**One timeout in that round is the *correct* answer, and it validates the shape
of the fix.** The freshly built candidate came from `origin/main`, which does not
carry the fix, so its own CLI still hung — **and the comparison succeeded
anyway**, because the fix that matters lives in the *caller*:
`classify_config_with_slot` closes stdin. That is why the block ships two
changes rather than one. A machine running the fixed code can stage a candidate
whose code is still broken, which is exactly the situation every update is in.

**Three defects were found in the spike itself before it ever ran on the rig**,
by building a local harness — a fake `uv`, a fake slot CLI, a real one-commit
repository — instead of trusting that it parsed. `ClassificationComparison`
carries only `proceed` and `reason`, so reading `.active`/`.candidate` would have
raised and lost the whole dry run; the suppression verdict required exit 0, which
`check-config` without `--json` never gives when a config is not `ready`; and the
link/cleanup path needed exercising because it points at the live installation.
**A probe written to diagnose a defect is code, and it earns the same treatment
as the code it is diagnosing.** Its own fix-marker was wrong twice more —
`args.command == "check-config"` and `getattr(args, "json", False)` both exist in
the pre-fix source — which is worth stating plainly: **a marker that also matches
the old version reports the fix installed everywhere**, and it did, for two slots
that predate it.

**Cleanup on a machine that can be bricked gets an explicit refusal.** The scratch
root contains a junction to the live `env-a`; a recursive delete that followed it
would destroy the running install. The spike removes the link first and **skips
the delete entirely** if it survives, printing the `rmdir` to run by hand, and
reports whether `env-a` is intact either way. That path was exercised off-rig,
which is why link creation is a junction on Windows and a symlink elsewhere.

**Still not reached by any probe, and genuinely the gate's job**: the launcher's
relaunch loop, activation at the next desktop launch, rollback reporting, and the
browser's banner states. Those need a real desktop lifecycle.

## 58e demo gate round 3 — 2026-08-27, STOPPED at Restart, and four limbs finally have evidence

**Staging works.** The Direct phase built and published a real pending slot —
`staging: "staged"`, `pending: "b"`, `pending_staged: true`, and
`automatic_restart: false`, which is the *correct* answer for a launch that is
not launcher-owned. That is the first time the block's central mechanism has
completed on hardware, and it is what four spike rounds bought.

**What stopped the round was a third, different defect — a cascade with two
poisons where the previous fix had cleared only one.**

1. `NotReady` legitimately refuses a candidate and records
   `comparison_refused_commit` plus its reason. It passed.
2. `Stage` ran 30 seconds later. The gate's `clear_build_failure_cache` cleared
   `build_error`, `build_failed_commit` and `build_error_detail` — **not**
   `comparison_refused_commit`. So `/api/update` still reported
   `comparison_refused: true`, and by item 9's own contract the banner showed
   *"this update needs the maintainer"* rather than the Restart controls.
3. The staging job then ran for 16 seconds, **failed, and recorded nothing at
   all**: no `build_error`, no `staging: "error"`, status stuck on `"running"`,
   no pending slot. The route's handler skipped its error record whenever
   `comparison_refused_commit` matched the candidate — and that key is proof of
   *some* refusal of that commit, never of *this* attempt.

**The product defect is the serious half, and it is not about the gate.** For a
real user: refuse one commit legitimately, repair the config, retry — and from
then on every failure of that commit is invisible, with `staging` frozen on
`"running"` and nothing to read. `stage_inactive_slot` now raises a typed
`ComparisonRefused` and the route suppresses its record **only** for that type;
every other failure is recorded unconditionally. The test fails on the old code
with `KeyError: 'build_error'`.

**The gate defect is mine, and it is the same shape as the one before it.**
Round 2 taught that one failed build short-circuits later phases, and the fix
cleared the build keys. There were two poison families, not one. **Clearing one
of two poisons is clearing neither**, and the second one silently changed what
the *browser* displayed — the operator was looking at a banner that was
correctly rendering poisoned state. `clear_staging_verdict` now clears both, and
**the NotReady phase cleans up the refusal it deliberately causes**, after
capturing it as evidence. A phase that breaks state on purpose puts it back.

**Four limbs now have rig evidence** — Direct staging, NotReady's refusal, the
two-slot comparison (`proceed: true`, both `ready`, isolated imports resolving to
each slot's own `site-packages`), and the **ordinary exit pause**: four server
PIDs present while the prompt was displayed, none of them present after Enter.
Restart, Restart later, activation, rollback and the banner's progress states
still have none.

**Said plainly, because it matters for round 4: the fix repaired the mechanism
that *hid* the Stage failure, not necessarily the failure itself.** Direct staged
successfully forty seconds earlier, so whatever went wrong is specific to running
Stage immediately after NotReady's stub-swap. Round 4 will produce
`build_error_detail` naming the failing `uv` subcommand and its stderr, which is
the first time that question will be answerable from the artifacts. The
recommendation given to the operator is to run **only through `-Mode Stage`** and
stop — ten minutes instead of an evening, with a decisive answer either way.

**A spike wart worth recording.** Round 5's suppression control held for 42.58 s
and then exited 0 because the operator pressed Enter in the console; the verdict
keyed off the `TIMED OUT` marker and would have called that inconclusive. **Judge
a control by what it measured, not by how it was terminated.**

## 58e demo gate round 4 — 2026-08-27: the failure named itself

The first round where a staging failure produced a diagnosis instead of silence,
and the round-3 fixes are what made that possible. Every one of them worked:
all six poison keys cleared, `comparison_refused` false throughout, and the
staging job recorded `build_error: "slot emitted invalid config-classification
JSON"` with `staging: {"status": "error"}`. **That was round 3's silent
sixteen-second failure too** — the same defect, finally legible.

**`input()` writes its prompt to stdout before it reads.** `pause_on_exit` calls
`input("\nPress Enter to close this window...")`, so a candidate slot that still
registers the pause emits

```
{"classification": "ready", ...}
Press Enter to close this window...
```

and a whole-stream `json.loads` fails. **This is a defect I introduced.** The
`stdin=DEVNULL` fix from round 2 stopped that call from *hanging*; it did not
stop it from *printing*, and a hang became corrupted output. Every test in the
suite fed the parser clean stdout, so nothing could see it.

**The contract was wrong, not just the code.** `classify_config_with_slot`
invokes **a different version of microclaw by design** — the candidate is, by
definition, code that predates whatever we just fixed. Requiring its entire
stdout to be JSON makes every future update hostage to the *old* version's
politeness. It now lifts the first JSON object out of the stream with
`raw_decode` and ignores what surrounds it, and still refuses output containing
no usable object. The four new cases fail on the old code with the rig's exact
error, `JSONDecodeError: Extra data: line 3 column 1`.

**Generalise it, because this will recur**: *when you shell out to another
version of your own program, treat its stdout as untrusted framing.* The one
thing you may rely on is the payload you can find inside it.

**Two fixes of mine were confirmed working on this round, which is worth stating
separately from the failure**: the staging verdict is cleared per phase, so no
phase inherits another's refusal; and a failure that is not a typed
`ComparisonRefused` is always recorded. Round 3 had neither, and the operator
had to `Ctrl-C` out of a phase that would never finish. Round 4 finished on its
own.

**Prediction recorded before the next run**, so it can be scored rather than
rationalised afterwards: Stage will build, the comparison will return
`ready`/`ready`, pending will publish, and the banner will offer **Restart now**.
If it does not, `build_error` and `build_error_detail` will say why.

## 58e demo gate round 5 — 2026-08-27: Restart now works, and the slot it activated could not start

**The prediction recorded before this round held exactly.** Stage built, the
comparison returned `ready`/`ready`, pending published, `automatic_restart`
turned true, and the Restart control appeared for the first time.

**Restart now works.** One new launch line with a fresh nonce, the restart
request consumed, `active` flipped `a` -> `b`, pending consumed,
`installed_commit` reconciled to the staged commit, and the marker beside the
running interpreter matching it — **8.1 seconds, with no stall on the exit
pause**. Four limbs that had never run anywhere now have rig evidence: Restart
now, the nonce-matched relaunch, activation, and the commit reconciliation this
block added.

**And then the update bricked the application.** The activated slot answered
`` `microclaw serve` needs fastapi and uvicorn ``. Two defects, one enabling the
other:

- **`stage_inactive_slot` installed `str(source)`; `install.bat` installs
  `.[serve]`.** Every staged slot has therefore always lacked fastapi and
  uvicorn, and the desktop icon runs nothing but `serve`. **Every update this
  design has ever produced would have broken the application on activation.**
  Nothing caught it because no test and no gate had ever *started* a staged
  slot — staging itself only began working two rounds ago.
- **The bounded smoke check the design has specified since the beginning was
  never implemented.** §"Put the updater outside the environment it replaces"
  requires `import microclaw`, a CLI parse and packaged web assets before
  pending is published; the code only ever ran the config classification. A
  staged slot must now import `microclaw`, `microclaw.webserve` **and
  `uvicorn`** — the last named explicitly because `serve` imports it lazily,
  which is exactly the difference between catching a missing extra and shipping
  one — and a slot that fails is refused, never published, and records why.

**The rule this earns**: *an installer and an updater that build the same
application must build it the same way.* Two independent specifications of what
"install microclaw" means will diverge, and the one nobody watches is the one
that runs unattended on a user's machine.

**Rollback did not rescue the operator, and that is worth recording separately.**
The failed child did not exit: it sat at `Press Enter to close this window...`,
so the launcher waited its full 30-second health timeout. An operator who closes
that console first kills the launcher too, and the machine stays on the broken
slot — which is precisely what happened here, since the next desktop launch
produced the same refusal. §"Put the updater outside the environment it
replaces" already warns that closing the console may kill both processes; this
is the first time it has cost anything. **The 30-second timeout branch now has
rig evidence** — the row left open after 58c can be ticked, though not in the
way it was meant to be earned.

## 58e demo gate round 6 — 2026-08-27: rollback and the PyPI failure pass

Run against a slot that predated the `[serve]` fix, so its downstream limbs were
lost to the defect round 5 found. Two limbs it did prove outright, both for the
first time:

**Failed start rolls back, and reports on the *next* launch.** The deliberately
broken slot launched (`slot=a nonce=568dd61a…`), failed, and
`rollback-report.txt` appeared with **no `rollback-reported=` line on that
launch**; the following launch (`slot=b nonce=0b7645dd…`) emitted
`rollback-reported=Microclaw rolled back from slot a to slot b.` and reached
nonce-matched health. The deferred-report contract is now evidenced end to end —
58c only ever saw the `child-exited` half of it.

**An unreachable index is cached without touching the selector**: `build_error`
set, `staging: {"status": "error"}`, no pending published, active slot unchanged,
retry deadline retained. The session-scoped `UV_INDEX_URL` reached the staging
worker exactly as intended and left nothing behind this time.

**`later` and `closed` reported NOT EXERCISED, and both are the same defect.**
The active slot predated round 5's `[serve]` fix, so the staged slot again had no
fastapi and no uvicorn: `restart.json` shows an empty `health`, nothing was
answering on 8000 for `closed`, and the restart had already consumed the pending
slot `later` needed. Worth stating because it is easy to misread as two more
failures: **one product defect accounted for both**, and the fix does not depend
on what the *source* commit contains — `[serve]` is a property of how staging
installs, not of the code being installed.

**Offline cannot be gated over Remote Desktop**, which is how the operator
reaches the demo machine. Disconnecting the network ends the session that would
observe the result. It needs physical access and is booked for that, not
simulated: an approximation that breaks only the recorded `git_executable` would
exercise a failed *check*, not a disconnected machine, and this design has been
punished more than once for probes that measured something adjacent to the
question. See §"Owed evidence that cannot be booked".

**The gate's phase ledger assumes one continuous run**, and this round exposed
the cost: `Verify` scores one evidence directory, so limbs proved in an earlier
directory do not carry forward. With Prepare now cheap and Stage taking ~15 s,
the answer is a single clean run rather than machinery to merge folders — but it
is worth knowing before someone tries to resume a half-finished gate.

## 58e demo gate round 7 — 2026-08-27: 10 PASS, and all three failures were the scorer

The first round to reach every phase but two. Reported 10 PASS / 3 FAIL / 3 NOT
EXERCISED; **replaying the operator's artifacts through the corrected scorer
turns all three failures into NOT EXERCISED**, with `git diff` over `microclaw/`,
`install.bat`, `scripts/` and `tests/` **empty** since the round's build. No
product defect was found in this round.

**Four limbs passed for the first time**: the **public ZIP** (`public-head`,
installed commit `unknown`, the private-repository 404 cached), **Restore**
(commit, selector, pending state and the `%APPDATA%` hash all returned), the
**progress-then-ready banner states**, and the **one-job refusal against a
genuinely running build** — 269 `staging: true` samples with the second request
refused 409.

**The three scoring defects, because each is a distinct lesson.**

1. **A rebuild is not a byte change.** The limb required the inactive slot's
   executable and marker to differ after staging. Staging the *same* commit
   twice produces a byte-identical console-script trampoline and a byte-identical
   marker — so identical bytes are what a **successful** rebuild looks like when
   `Direct` and `Stage` both stage `origin/main`. Scored on `pyvenv.cfg`'s mtime
   now, which is what `uv venv --clear` actually moves.

2. **The launcher logs its line before it starts the child.** The restart limb
   waited for a new log line and snapshotted immediately, so the health marker
   could not yet exist. **Rounds 5 and 6 reported an empty `health` for the same
   reason and it was read as a product failure both times.** The phase now parses
   the nonce out of that line and waits for `launch-health.txt` to carry it. A
   probe that samples at the moment a *log entry* appears is measuring the log,
   not the thing the log announces.

3. **After an activation, the gate is testing the other build.** `Restart` and
   `Later` activate the staged slot — built from `origin/main`, which does not
   contain this block — so every phase after them ran against code without
   `build_error_detail`, and its absence scored as a product failure. **The last
   four phases of the gate were testing the wrong version.** The runbook now
   returns to the branch slot with a literal command before the diagnostic
   phases, and the limb reports NOT EXERCISED naming both commits rather than
   asserting through the difference.

**A scorer must degrade to NOT EXERCISED on evidence that predates it.** Two of
these fixes read fields older artifacts do not carry; each limb now checks for
the field and reports NOT EXERCISED with the command that would produce it. That
is what let this round be re-scored from the returned folder instead of booking
an eighth trip, exactly as 58d's round 1 was.

**Remaining after this round**: `Restart later`, `Micro-Manager closed`, and
`offline` — the last needing physical access to the machine.

## 58e's combined rig evidence — scored across rounds 6, 7 and 8, 2026-08-27

**Eight gate rounds and five spike rounds is more than this block should have
cost, and the operator called it: "brutal doing this many gates. we need to wrap
this up."** That is the right call. The gate scores one evidence directory, so
each round re-proved what came before it; the honest way to close is to score
the limbs across rounds, having first verified the mechanisms were identical.

**The diff check, done before accepting split evidence, as 58c requires.**
`git diff 7d6b919 HEAD -- microclaw/ install.bat scripts/ tests/` is **empty**:
rounds 7 and 8 ran the product code that is on the branch now. For round 6's
rollback limb, `scripts/updater-launcher.ps1` and `microclaw/shortcut.py` are
unchanged since `c46e2db`, and no rollback path in `updates.py`
(`rollback_slot`, `consume_rollback_report`, `wait_for_launcher_health`,
`health_matches`) differs. The rollback evidence therefore carries.

| Limb | Proved | Evidence |
| --- | --- | --- |
| Direct executable stages, offers Restart later only | R7, R8 | `pending_staged` true with `automatic_restart` false on a non-launcher-owned process |
| One staging job; second refused | R7, R8 | 202 then 409 across 66–269 `staging: true` samples |
| Progress then ready banner states | R7, R8 | `staging` true before `pending_staged` true |
| Not-ready comparison banner | R7, R8 | real `stage_inactive_slot` refusal, candidate CLI a gate stub |
| Two real slot CLIs over one config | R7, R8 | `proceed: true`, both `ready`, isolated imports per slot |
| Active files locked, inactive rebuilt | R8 | active bytes identical, `pyvenv.cfg` mtime advanced |
| **Restart now, one nonce-matched relaunch** | **R8** | **5.60 s, health carrying that launch's nonce, request consumed, commit reconciled** |
| Ordinary Ctrl-C keeps the exit pause | R7, R8 | four child PIDs present at the prompt, none after Enter |
| Failed start rolls back, reports next launch | R6 | `rollback-reported=` absent on the failing launch, present on the next |
| Unreachable index cached, selector untouched | R8 | `build_error` + `uv pip exit 2: …`, no pending, active unchanged |
| Restore returns production state | R7 | commit, selector, pending and `%APPDATA%` hash all back |
| Public ZIP records `public-head`/`unknown`/404 | R7 | fresh ZIP install's cached state |
| **Micro-Manager closed keeps the slot** | R10 | staged slot activated and kept, no rollback, no relaunch |
| **Restart later activates at the next launch** | — | owed; the activation path itself is evidenced by R8 and R9 |
| **Offline launch** | — | owed; needs physical access |

**Thirteen of fifteen limbs have rig evidence** (twelve at the time this table was first written; round 10 added Micro-Manager closed). Of the remainder:
`Restart later` exercises the *same* `activate_pending` call in the same
launcher path that round 8 proved when the restart flipped `a` -> `b` at launch —
what is untested is only that it happens on a manual launch rather than a
launcher-driven one. `Micro-Manager closed` is a genuine gap: nothing has yet
proved that a slot which reaches health and then exits on the bridge check is
**kept** rather than rolled back or relaunched. Offline is a genuine gap that
needs someone at the machine.

**Two gate defects this round, both cheap and both fixed.** `-Mode Closed` hung
for fifteen minutes because the public-ZIP install had left no cached candidate:
`/api/update/stage` answered 409 and the phase polled for a job that did not
exist. Every staging phase now checks the POST was accepted first. And the
public-ZIP limb demanded a cached 404 that only exists after the install has run
its check once, so a fresh install now reports NOT EXERCISED with the command
that produces it. **A phase that polls without checking the request was accepted
will always hang rather than fail**, and hanging is the most expensive failure
mode a gate has: it costs the operator's evening, not a line in a report.

## 58e demo gate round 10 — 2026-08-27: Micro-Manager closed passes, and the block closes

The short close-out run. **`Micro-Manager closed` PASSES under the strengthened
limb** — one healthy launch, the staged slot activated and *kept*, no rollback,
no relaunch: `one healthy launch; active remained b`. That was the last genuine
gap, and it is the limb §"Put the updater outside the environment it replaces"
singles out as the one most likely to be built as a relaunch loop.

It matters that this pass is trustworthy where round 9's was not. Round 9 passed
the same limb while **nothing had been updated** — its staging had failed, so no
slot was activated and an ordinary launch satisfied every condition. The limb now
requires a `pending_staged` sample, a pending selector before the launch, that
exact slot active afterwards, and no rollback report. Round 9's evidence would
report NOT EXERCISED against it.

**Three defects in the run instructions were found and fixed *before* this round
rather than by it**, which is the only reason it took ten minutes:
`Prepare` was not clearing `last_attempt`/`next_check`, so no candidate would
have been cached and every staging phase would have been refused (round 9 had
survived that only because a `Restore` happened to clear them first); the
Micro-Manager-closed step said to close Micro-Manager *before* a phase that
stages through the live server, which cannot start without a bridge; and both
`Closed` and `Later` end with the `origin/main` slot active, so running one after
the other tests the wrong build — which is exactly what wasted round 9.

**`Restart later` was not run** and is recorded as owed. Its mechanism is not
unevidenced: `activate_pending` consuming a pending selector at the start of a
desktop launch is what round 8's restart proved when the selector flipped
`a` -> `b`, and round 9 demonstrated it *accidentally* — the selector moved
`a` -> `b` between `Prepare` and `Stage` because a leftover pending slot was
activated by an ordinary icon launch. What has no direct limb is the operator
clicking **Restart later** and seeing that same activation at the next manual
launch.

### Closing tally

**Thirteen of fifteen limbs have rig evidence**, scored across rounds 6, 7, 8 and
10 with the product-diff check in §"58e's combined rig evidence". Owed:
`Restart later` (mechanism evidenced, the button path is not) and `offline`
(needs physical access to the machine).

**Ten gate rounds and five spike rounds.** The ratio is the story: **six product
defects, every one of which would have shipped**, against roughly twice as many
defects in the gate itself. In order — `uv venv` refusing an existing slot, so
every update after a machine's first would fail; a slot CLI hanging on an
inherited exit pause, so staging could never complete on a desktop launch; a
stale refusal silencing every later failure of the same commit; a candidate's
stdout framing breaking the classification parse; the missing `[serve]` extra,
which meant **every update this design produced left the application unable to
start**; and the absent smoke check that would have caught it. The last two were
found only because the earlier four were fixed first — each defect was hiding the
next one, which is why this took ten rounds and not three.

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
- **`Restart later`'s button path was never gated.** Staging leaves a pending
  selector and not restarting *is* Restart later, so the mechanism —
  `activate_pending` consuming that selector at the start of the next desktop
  launch — is evidenced twice over: deliberately in round 8's restart, and
  accidentally in round 9, where the selector moved `a` -> `b` between two gate
  phases because an ordinary icon launch consumed a leftover pending slot. What
  is owed is the operator pressing the button and seeing that activation, which
  is a UI path over a proven mechanism.
- **The offline limb needs physical access to the demo machine.** The operator
  reaches it over Remote Desktop, and disconnecting the network ends the session
  that would observe the result. Booked for a visit rather than approximated:
  breaking the recorded `git_executable` would exercise a failed *check*, not a
  disconnected machine, and a probe that measures something adjacent to the
  question is how this design lost two rig trips already.
- **Nothing is owed to a microscope.** No limb of design/58 needs M2, M5 or a
  Nikon. 58b's optional fuller-config limb is a convenience, not a debt.

## Post-merge design gate

Run after 58c, 2026-08-27. The rows that need 58d/58e are marked as such.

- [x] Branch protection: **the escape was taken, not the ruleset.** 58-P is
      deferred to the public flip by operator decision 2026-08-27, and
      §"`main` is the release branch" records the two 403s verbatim. There is no
      ruleset id to record because the API refuses to create one on a Free
      organization's private repository.
- [x] §"Track the private clone's upstream for now" was amended during 58a: it
      now says `branch.main.remote` and `tracked_branch: main`, not the clone's
      current branch. The demo machine remains the case that proves it — its
      checkout sat on the block branch while discovery correctly tracked
      `origin/main`.
- [x] §"Put the updater outside the environment it replaces" now says what
      happens to a conda, miniforge or embedded-Python install, what the
      detection can and cannot reach, and that an unmanaged install stays silent
      rather than broken. It also records that an interpreter which exists is not
      one that runs.
- [x] `update-state.json` and `microclaw-slot.json` field lists reconciled
      against the code, in that same section, including which two fields are
      load-bearing and what broke when the installer invented its own record.
- [x] Folded into `CLAUDE.md`: *only the thing outside both slots may write the
      thing outside both slots*; *a marker's existence is not health*; *a gate
      must not leave production state pointing into its own evidence folder*;
      and *when a block inserts a step before an existing one, test the state
      handed over*. Windows layout details stayed here.
- [x] `design/35`'s live-state note moved onto design/58's state at the 58c
      merge, and both pointers to it updated.
- [x] Carried-forward register: **no row moves.** design/58 adds no tool, so the
      eleven-undecorated-tools row is untouched, and nothing else in the register
      is about the updater.
- [x] **58d's half done**: the `/api/update/*` route list, the response field
      list and the banner copy are reconciled in §"Check quietly, ask where the
      user already is", including the two things that ship as routes without UI
      and the one caller the checklist never named. **58e still owes** the
      terminal line and the restart/relaunch half of that section.
- [ ] **After 58e**: reconcile `update-state.json`'s field list in §"Put the
      updater outside the environment it replaces" with `build_error_detail`,
      added at 58e gate round 1 so a staging failure names the command that
      failed rather than only the sentence the banner shows.
- [x] **After 58e**: the 30-second health **timeout** branch got rig evidence in
      round 5, though not as intended — a slot that could not start sat at the
      exit pause instead of exiting, so the launcher took the timeout path
      rather than `child-exited`. Recorded in §"58e demo gate round 5".

## Run ledger

| Block | Depends on | Branch | Start commit | Implementation | Gate | Merged | Design reconciled |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 58-P | public flip | n/a (repo config) | — | **not a blocker** — deferred to the flip by operator decision 2026-08-27 | n/a | — | — |
| 58a | — | ~~`design58/discovery`~~ | `4103d36` | `84d49cb` → `7c3a71f`; coordinator `9c087e2`, `b2f1e58`, `70650f0`, `1ccb677`; codex, **4 rounds, 13 findings** | **PASS** demo, 2026-08-26, **4 rounds** — 3 failed on gate defects, all 9 limbs on the 4th | `33028e9` 2026-08-26 | done — this section |
| 58b | — | ~~`design58/classification`~~ | `1a582dc` | `a462032` → `f50fd82`; coordinator `30b0d9b`; codex, **2 rounds, 6 findings**, 3 turns killed mid-flight | **PASS** demo 2026-08-27 — 16 PASS / 0 FAIL / 1 NOT EXERCISED; verdict INCOMPLETE **by design**, awaiting 58c | `3baec05` 2026-08-27 | done — this section |
| 58c | 58a, 58b | ~~`design58/two-slots`~~ | `83bbec7` | `01b634f` → `6492083`; codex **2 rounds, 17 findings**, 1 turn killed mid-flight; coordinator `d96d3f3`, `a665a2a`, `f51b4bd`, `2f23854`, `c2dfae5`, `df83d56`, `ea4fbc7`, `d70cb5c`, `07f177b`, `d5fa047` | **PASS** demo 2026-08-27, **3 rounds** — round 1 failed at limb 1 on two real `:make_env` defects; rounds 2+3 all eleven limbs at identical product code | `d1e08df` 2026-08-27 | done `d08433a` 2026-08-27 — §"Post-merge design gate", two rows left open for 58d/58e |
| 58d | 58a, 58b | ~~`design58/endpoints`~~ | `5044ae9` | `f066718` → `a111ddf`; codex **1 round, 11 findings**, the revision turn killed by an OpenAI usage limit *after* landing every edit; coordinator `a111ddf`, `3e7ce51`, `65d1ded` | **PASS** demo 2026-08-27, **1 round** — 12 PASS / 0 FAIL / 1 NOT EXERCISED (the Restart now button, 58e's); both non-passes were gate defects, re-scored by replaying the returned artifacts | `8529859` 2026-08-27 | done — this section |
| 58e | 58c, 58d | `design58/restart` | `651218f` | `7e584af` → `d2b646d`; codex **3 rounds, 32 findings**, 1 turn killed early and discarded; coordinator `aabbe4e`, `c46e2db`, `c6c8802`, `0885a52`, `d2b646d` | rounds 1 and 2 **STOPPED** demo 2026-08-27 (`uv venv` refused an existing slot; then the slot CLI hung on the inherited exit pause). **Four spike rounds replaced four gate trips**; round 3 **STOPPED** at Restart on a stale `comparison_refused_commit` that both hid the banner's controls and silenced the staging job's own failure. Round 5: **Restart now works** (8.1 s, nonce-matched, reconciled) — and revealed that every staged slot was built without `[serve]`. Round 6 added **rollback with its deferred report** and the **unreachable-index** limb; round 7 reached 10 PASS re-scored from artifacts; **round 8 proved Restart now's nonce-matched relaunch in 5.60 s**. Scored across rounds, **12 of 15 limbs have rig evidence**; `Restart later`, `Micro-Manager closed` and `offline` remain | — | — |

**Baseline on `main` at `feb0565`, coordinator-measured: 2182 passed / 99
skipped / 3 warnings** (macOS). Windows reads the same collected total with a
different skip split. **Gate on zero failures, never the count.**

## Resuming this block cold

Everything needed is on `main`.

**State as of 2026-08-27. 58a, 58b, 58c and 58d are MERGED. Next block is 58e**,
the last one, which depends on both 58c and 58d. Its branch is `design58/restart`.
**58e is the second block that can leave a machine unable to start**, so read
§"What made 58c different" below before assigning it.

### Read these before assigning 58e, in this order

1. `CLAUDE.md` §"The block workflow" — authoritative; **58a rewrote step 6** and
   **58c added §"Four rules the updater block paid for"**. Read its §"six
   contracts" preamble too, whose fixture rule 58a added — 58d hit that same
   fixture rule for the third time in this design.
2. This file: §"58c demo gate rounds 2 and 3" and §"58d demo gate round 1"
   (seven gate defects between them, and why a passing limb can prove less than
   its item claims), then the `## 58e` checklist section. §"Check quietly, ask
   where the user already is" is the specification for 58e's half too — the
   terminal line and the restart — and is more precise than the checklist; its
   reconciled "what actually ships" paragraph records what 58d already built.
3. `design/prompts.md`, the design/58 entries — 58b's carries the runner
   interruption recovery and the `fails_if` idiom; **58c's carries the five gate
   defects and the replay habit**, which is the part worth copying; **58d's
   carries the two reachability defects and the warning that strengthening a test
   can delete the assertion that had teeth**.
4. **The four gate scripts on `main` are the template**, in ascending order of
   quality: `design/58-block58a-demo-gate.py`, `-58b-`, `-58c-`, `-58d-`, each
   with a thin `.ps1`. Do not design 58e's gate from scratch. From 58b take the
   **mandatory `fails_if`** argument on the `limb` decorator; from 58c the phase
   ledger (`phases.json`), the `Verify` preflight that names the commands still
   owed, `need()` so absent evidence reports NOT EXERCISED rather than FAIL, and
   the habit of **replaying returned evidence through a changed scorer** before
   shipping it — 58d's round 1 was closed entirely by that replay. From 58d take
   the split between a program that scores and a runbook that asks a person to
   judge, and the habit of **writing a limb for evidence you are already
   collecting**.
5. Code 58e touches: `microclaw/updates.py` (`activate_pending`,
   `fresh_launch`, `rollback_slot`, `wait_for_launcher_health`),
   `scripts/updater-launcher.ps1`, `microclaw/webserve.py`'s
   `POST /api/update/restart` — whose idle checks 58d shipped and whose
   `501 {"restart_requested": false}` seam is the one line 58e replaces — and
   `microclaw/__main__.py` for the terminal line. The `update-state.json` and
   `microclaw-slot.json` field lists are reconciled in §"Put the updater outside
   the environment it replaces", the route and banner contract in §"Check
   quietly" — read both before adding a field or a route.

### What 58e inherits, and the four traps in it

- **`POST /api/update/restart` already exists and already refuses.** Its four
  idle checks, its offerability refusal and its `501` seam shipped in 58d, with
  the seam marked in a comment. 58e replaces that one return; it does not
  rewrite the route, and the four idle predicates are not to be re-derived —
  each reads the object that owns the condition, and one of them (the
  acquisition ledger) is reachable **only** through
  `tools._existing_acquisition_ledger`, never through an attribute on `ctrl`.
- **58e owes the rig evidence 58d could not take.** The Restart now *button*,
  `/api/update/stage`'s one-job refusal, the not-`ready` banner of item 9, the
  progress and restart banner states, and the config comparison **from a real
  staging run** all exist and are unit-tested with no demo-machine evidence,
  because staging publishes a pending slot the next launch acts on. 58e's gate
  is where they land, and its limb list should name them explicitly rather than
  assume the end-to-end run covers them.
- **The gate needs the same `installed_commit` arrangement 58d used**, or a
  merge first: an unmerged branch is not an ancestor of `origin/main`, so
  discovery correctly reports `diverged` and there is nothing to update *to*.
  See the note under §"Gate — demo machine, `design/58-block58d-demo-gate.md`".
- **The agent must not be able to trigger its own replacement.** Update state is
  operational UI state, not conversation history and not a model tool. 58d's
  identity test is `test_update_endpoints_do_not_enter_agent_state`; extend it
  rather than writing a second one, and note that `_durable_history` prefers
  `session.store`, so an assertion on it alone cannot see a row written straight
  to `session.history`.

### What made 58c different from 58a and 58b — kept, because 58e inherits it

58e restarts the server and activates a pending slot, so it is the second block
that can leave a machine unable to start. Everything below applied to 58c and
applies again there.

- **It is the only block so far that can brick an install.** Its gate replaces the
  managed environment, kills the server and forces rollbacks, repeatedly. The
  runbook must back up `%APPDATA%\microclaw` and the existing
  `%LOCALAPPDATA%\microclaw\env` first, as a literal command that prints the
  copy.
- **Its `updater-launcher.ps1` cannot run on CI at all**, which is the exact
  property that let 58a's defect through four review rounds. **Put the state
  machine in Python where it is unit-testable — activation, pending consumption,
  nonce matching, stale-marker rejection, slot-metadata mismatch, rollback, the
  protocol-too-old refusal — and keep the `.ps1` thin.** Whatever remains only in
  PowerShell is gated, structurally tested, or admitted as untested. **Round 1
  inverted this**: the tested functions were called by nothing but their own
  tests while the `.ps1` reimplemented all of them. Grep for callers outside
  `tests/` before believing a "tested state machine" claim.
- **It discharged 58b's one debt**: two real slot validators classifying one
  shared config file, both `ready` on the demo machine.

State:

- **58a is MERGED** (`33028e9`) and its branch, worktree and gate are closed.
  `main` measures **2220 passed / 99 skipped / 3 warnings** (macOS,
  coordinator-measured). Its round history is §"58a demo gate round 1" and
  `design/prompts.md`.
- **58b is MERGED** (`3baec05`, 2026-08-27), branch and worktree deleted. Its
  demo gate ran 16 PASS / 0 FAIL / **1 NOT EXERCISED**, verdict INCOMPLETE — the
  correct result, not a failure. `main` measures **2244 passed / 99 skipped / 3
  warnings** (macOS, coordinator-measured), from 2220.
- **58c is MERGED** (`d1e08df`, 2026-08-27), branch and worktree deleted. `main`
  measures **2271 passed / 99 skipped / 3 warnings** (macOS,
  coordinator-measured), from 2244. Three demo-gate rounds; see §"58c demo gate
  round 1" and §"58c demo gate rounds 2 and 3". **58b's debt is discharged.** Two review rounds, seventeen findings. Its gate
  ships as a runbook **and** a program — see §"Gate — demo machine, a runbook
  **and** a program" — and the program runs in five phases (`Prepare`,
  `Healthy`, `Closed`, `Rollback`, `Verify`), each one command, with the human
  only operating the desktop icon and Micro-Manager.
- **58d is MERGED** (`8529859`, 2026-08-27), branch and worktree deleted. `main`
  measures **2296 passed / 99 skipped / 3 warnings** (macOS,
  coordinator-measured), from 2271. **One** demo-gate round, 12 PASS / 0 FAIL /
  1 NOT EXERCISED by design; the machine reported one FAIL and one NOT
  EXERCISED and **both were gate defects**, re-scored by replaying the returned
  artifacts with `git diff` over the product tree empty. One Codex round,
  eleven findings, three of them product defects. See §"58d demo gate round 1".
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
- **58-P blocks nothing** (operator decision 2026-08-27). `main` is the release
  branch; the boundary is the block workflow's own pre-merge testing on multiple
  machines, which is more than a CI check would prove. Branch protection and CI
  are additions for the day the repository goes public, when they first become
  possible on the Free plan. **Do not re-raise this as a blocker.**
- **Every gate is the demo machine.** The Nikon is gone and no limb of this
  design needs a microscope at all.
