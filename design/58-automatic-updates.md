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
worktree, record its canonical path, repository identity, current branch,
configured upstream (normally `origin/main`), and installed commit. Do not infer
these later from a `.git` directory that merely happens to be above the package.

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
`zacsimile/microclaw` and branch `main`, follows no URL supplied by the browser,
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
fetches may follow GitHub's normal Git redirect, but discovery likewise verifies
the fetched source against the recorded repository identity before staging.

Moving `zacsimile/microclaw` into a Micro-Claw organization before shipping is
still the simpler operational choice, but it is not a blocker. Verification by
immutable identity is what makes requirement 3 survive either order; trusting
the old owner/name alone would be unsafe if that namespace were later reused.

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
