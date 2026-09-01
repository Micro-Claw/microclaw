# Installable extensions — optional dependencies from the GUI

## Problem

`microclaw[ilastik]` is correctly not installed by default: most users never
touch ilastik, and `h5py` drags HDF5 onto every rig that does not need it. But
the only way to get it today is the sentence
`ilastik_adapter.py:371` prints — *"install the microclaw[ilastik] extra in
Microclaw's environment"* — which asks a microscopist to find
`%LOCALAPPDATA%\microclaw\env-a`, open a shell, and know what an extra is. On a
managed install they must also know **which slot is active**, and an install
into the wrong one evaporates at the next launch.

The same wall stands in front of contributors. A skill that wants `napari`, or a
plate reader that wants `pandas`, has two options: add the dependency to the
default install and tax every rig, or write another sentence nobody can act on.

There is a second, larger ownership problem. If accepting a community skill
means copying its files and dependency pins into this repository, MicroClaw
silently becomes the maintainer of that skill. We would have to review routine
releases, update pins when Python or a package changes, and decide what to do
when the original author disappears. A separate repository to which authors
send pull requests changes the queue, but not the ownership: we would still
merge, publish, and appear to warrant every version in it.

There is also a product-state problem that a command-line installer can mostly
avoid. A GUI catalog looks authoritative. If it says a skill is available, an
install later fails, and the skill disappears on the next launch, the user sees
MicroClaw contradict itself. Network failures, yanked wheels, incompatible
Python versions, and abandoned releases are normal package-management states;
they must be represented rather than collapsed into "available" or "absent."

We already own machinery for exactly this shape of problem — locating uv,
building an environment, proving the built thing starts, carrying state across
two slots, and reporting all of it in a browser banner (design/58). The question
is whether an extension install can be a small use of that machinery rather than
a second one.

## Assessment of the proposal

Yes, with one correction and one narrowing.

**The correction for first-party extensions.** The plan's stated goal 1 — *"it
keeps project dependencies out of our pyproject.toml"* — is the wrong target,
and taking it literally removes the property that makes the feature safe.
Extras belong **in** `pyproject.toml`; what they stay out of is the *default*
dependency list, which is where the tax actually is. `pyproject.toml` is what
gives an extension a
resolvable version range, a review in the block workflow, and — decisively — a
place in the built wheel's own metadata. Requirement strings that live in a
`SKILL.md` instead are strings a contributor can change without touching
packaging, and there is then no allowlist: the install endpoint would be taking
package names from a document rather than from the distribution it is running.

So: **the unit of installation is a pyproject extra, and the allowlist is the
running distribution's own `Provides-Extra` metadata.** A skill *names* an extra;
it never names a package. Both halves are still committed to the repository, so
the plan's vetting property holds — it just holds through packaging metadata
instead of through free text. Goal 2 (a user cannot find the venv) is fully
served either way, and it is the goal that matters.

That conclusion applies only to code MicroClaw owns and imports in its own
process. It must not become the admission rule for third-party skills. Community
skills need a separate package boundary so their author, not MicroClaw, owns
their releases and dependency policy.

**The narrowing.** Do not reuse the *slot* half of design/58. Staging rebuilds
the inactive environment and requires a restart; that is right for replacing
Microclaw and wrong for adding `h5py`, which must be usable in the session that
asked for it. Reuse the smaller pieces — uv discovery, atomic state next to
`update-state.json`, the job lock and banner pattern, "prove it by running it" —
and install into the environment that is running.

## Decision

### Split first-party extensions from community skill packages

Keep the extra-based mechanism in this design for first-party integrations such
as `ilastik`: they extend MicroClaw code, run in MicroClaw's process, and
therefore must be reviewed and constrained with MicroClaw itself.

Add a separate community package mechanism rather than accepting community
skills into this repository. Its units and ownership are:

```text
publisher-owned source repository
        |
        | CI builds, tests, signs and publishes an immutable release
        v
skill package repository / registry
        |
        | catalog metadata points to package + exact release + digest
        v
MicroClaw stages package and its locked environment, verifies, then activates
```

A skill package contains its `SKILL.md`, assets, a manifest, and a dependency
lock for every supported platform/Python combination. The manifest includes a
stable package ID, publisher identity, version, compatible MicroClaw and Python
ranges, entry points/capabilities, license, source and issue URLs, and the
release digest. Releases are immutable. The publisher produces new releases,
updates dependency locks, and marks obsolete releases as yanked. MicroClaw owns
only the package protocol, installer, trust policy, and catalog UI—not the skill
or its pins.

Do not make a pull-request repository of copied skill files the primary
solution. A lightweight registry repository is reasonable if it contains only
signed catalog records that point to publisher-owned artifacts. This gives us a
reviewable discovery and delisting surface without transferring maintenance.
The artifact store could initially be GitHub Releases or a Python package index;
we do not need to operate a ClawHub-like service to establish the boundary.
Publishing from a GitHub repository via CI is preferable to letting the GUI run
arbitrary npm or pip package names supplied by a catalog entry.

Dependencies for community skills do not enter MicroClaw's environment. Each
installed package gets an environment under the user data directory, keyed by
package ID and release digest. Installation uses the publisher's lock and
requires hashes; it never resolves an unbounded dependency set into the running
server. Package code executes out of process through a small, versioned protocol
with declared capabilities. A Markdown-only skill needs no environment. This
isolation lets a publisher choose and pin dependencies without upgrading
MicroClaw's `numpy`, and permits two skills to pin different versions. A
community package that requires in-process imports or new privileged MicroClaw
APIs is not a community skill; it is a first-party extension proposal and
follows the reviewed extra path above.

The first implementation can deliberately support only pure Markdown skills,
then add isolated executable entry points once the process protocol and
capability model exist. It must not temporarily install third-party dependencies
into MicroClaw's venv: that shortcut destroys the ownership and safety boundary
the package mechanism is meant to create.

### Treat GUI installation as a durable transaction, not a promise

The catalog uses distinct states and never equates discovery with usability:

- **Available** means the registry has a compatible, non-yanked release. It is
  an invitation to attempt installation, not a claim that this machine can
  install it.
- **Checking** performs local compatibility and disk checks and fetches the
  signed manifest/lock. The confirmation screen shows publisher, version,
  permissions, download size, and the registry's last successful verification
  platform/date.
- **Installing** downloads and builds into a temporary directory. MicroClaw
  verifies signatures/digests, locked dependencies, declared files, entry-point
  startup, and a package self-check before changing active state.
- **Installed / Ready** is written only after verification and an atomic rename.
  The receipt records the exact release, digest, lock digest, publisher, granted
  capabilities, verification result, and install time.
- **Install failed** is a durable state with the attempted version and a useful
  reason (network, incompatible platform, resolution, verification, or package
  self-check). The catalog entry remains visible with Retry, Report to publisher,
  and View details actions. Failure never creates a partially installed skill.
- **Needs repair** means a receipt exists but local verification fails at a later
  startup. Keep the skill visible and disabled, explain why, and offer Repair or
  Remove. Never silently delete it or present it as never installed.

Activation is transactional: keep the previous verified release active until
the candidate passes, atomically switch an `active` pointer, and retain one
previous release for rollback. On a failed update, continue using the previous
release. On a failed first install, remove only the staging directory. Registry
unavailability at startup uses a cached, signed last-known-good catalog and does
not affect already installed packages. A yanked or newly incompatible release
gets a warning; it is not remotely uninstalled.

The catalog should also lower the probability of offering a doomed install.
Only show releases whose declared OS, architecture, Python, MicroClaw, and
capabilities match locally. Registry CI should install and self-check each
release on supported targets and expose the result and age, but this remains
evidence, not a guarantee—local installation is still the authority. Default
sorting can favor verified and recently maintained skills, while abandoned
skills remain visibly third-party and may be delisted from discovery without
altering existing installations.

This preserves the ease of a GUI without pretending package installation is
infallible. The command-line path and GUI call the same installer/state machine;
the GUI is not a second package manager. A future CLI is useful for authors,
automation, and support, but users need not know it exists.

### An extension is an extra, named by the browser, resolved from our own metadata

```text
browser  --> POST /api/extensions/install {"name": "ilastik"}
                            |
                            v
        name in EXTENSIONS and in Provides-Extra?   -- no --> 400
                            |
                            v
        requirement strings from THIS dist's Requires-Dist
        ["h5py>=3.10"]        (never from the request, never from a SKILL.md)
                            |
                            v
        uv pip install --python <sys.executable>
                       --constraint <frozen current environment>
                       h5py>=3.10
                            |
                            v
        import it, in this process, and report what imported
```

The request body carries an **extra name and nothing else**. The strings that
reach uv's argv come from `importlib.metadata.metadata("microclaw")`, i.e. from
the `pyproject.toml` that was reviewed and built. That is the whole security
argument, and it is worth stating in one line in the code: *no package name in
this call came from the network.*

Refuse an extra whose requirements carry any environment marker other than the
`extra == "..."` clause. v1 does not evaluate markers, and silently dropping one
would install a requirement on a platform it was excluded from.

### Pin the current environment so an extension cannot move an installed package

`uv pip install h5py>=3.10` may upgrade `numpy` to satisfy it. Doing that to a
live server whose `numpy` is already imported — mid-session, possibly mid-
acquisition — is the failure mode this feature must not have.

Freeze first, constrain second:

```python
frozen = uv("pip", "freeze", "--python", python)        # exclude microclaw
                                                        # and any "@ " direct URL
install = uv("pip", "install", "--python", python,
             "--constraint", constraints, *requirements)
```

With every installed distribution pinned to its exact version, uv **cannot**
change one: an extension that needs a newer `numpy` fails resolution and reports
a conflict instead of performing one. This is a structural guard, not a parse of
uv's output, and it is why v1 does not need `--dry-run` to be safe.

Run `--dry-run` anyway, with the same constraints, only to show the user what
would be added before they press the button. If its output cannot be parsed,
show no preview — a preview is information, and losing it must never change what
gets installed.

### Prove the extension by importing it

uv exiting 0 says a wheel was unpacked. `h5py` with a mismatched HDF5 or numpy
raises on import — which is why `ilastik_adapter.py` catches
`(ImportError, ValueError)` and not `ImportError` alone. *A marker's existence is
not health* applies unchanged: readiness is

1. every distribution named by the extra's requirements is present, and
2. every top-level module those distributions provide
   (`importlib.metadata.packages_distributions()`, inverted — derived, not
   tabulated) imports.

After a successful install, call `importlib.invalidate_caches()` and run that
import **in this process**. That is both the proof and the thing the user
wanted: the session that asked can now use it, with no restart. A readiness
failure after a successful uv run is reported as such — installed, not usable —
and names the exception.

### Record extensions outside both slots, in their own file

`user_data_dir()/extensions.json`, beside `update-state.json`:

```json
{"installed": {"ilastik": {"at": 1756... , "commit": "<sha or null>",
                           "requirements": ["h5py>=3.10"]}}}
```

**Its own file, deliberately not a key in `update-state.json`.** Staging holds a
loaded state dict across a multi-minute build and writes it several times; an
install completing in that window would have its key clobbered by a stale
write. One writer, one file. Reuse `write_state`'s atomic replace-with-retry by
lifting it to a shared `_write_json_atomic` — the Windows share-violation retry
in it was paid for by 58e and must not be re-implemented.

This file is also what makes an unmanaged install (a developer checkout, macOS)
work identically: `user_data_dir()` exists everywhere, `update-state.json` does
not.

### Carry extensions through an update, and notice when they did not survive

`stage_inactive_slot` installs `f"{source}[serve]"`. With extensions recorded it
installs `f"{source}[serve,ilastik]"` — otherwise every update silently removes
the extension, which is *"a cache computed against the previous install"* in a
new costume.

**An extension must never be able to break an update.** If the combined spec
fails, retry with `[serve]` alone; if that succeeds, stage it and record the
extras failure for the panel. The smoke check stays exactly as it is — it proves
`microclaw`, `microclaw.webserve` and `uvicorn`, never an extension.

Three states leave a recorded extension unusable, and all three are ordinary:
a rollback to the previous slot, an `install.bat` reinstall (which builds
`.[serve]` and is deliberately *not* being taught to read JSON), and a staged
extras failure. So at startup, compare the record against readiness and, when
they disagree, say so in the panel with a Reinstall button. **No network at
startup and no automatic install** — the check is local, and the button is the
user's. This is the compensation for `install.bat` not carrying extras, and it
is honest: the app starts fine without an extension, so the "installer and
updater must build the same way" rule is not being broken where it bites.

### Fold uv discovery, which is currently thinner than the installer's

`updates.stage_cached_candidate` does `shutil.which("uv")`. `install.bat` does
`where uv` **and then** `%USERPROFILE%\.local\bin\uv.exe`, because that is where
its own bootstrap puts it and a process started before the PATH change does not
see it. Add `locate_uv()` next to `locate_git()` with both routes and use it
from staging as well. This is a pre-existing gap in staging, found here; it is
in scope because the extension path would otherwise reproduce it.

### A skill declares the extra it needs, and is never withheld

`SKILL.md` frontmatter gains one optional key:

```yaml
---
name: ilastik-segmentation
description: ...
requires: [ilastik]
---
```

`_parse_skill` accepts `name`, `description`, and optionally `requires` (a list
of nonempty strings). It does **not** check the names against installed
metadata — a malformed contributor edit must not make `SKILL_CATALOG` raise at
import time and take the whole application down. That check is a test.

`load_skill` returns the body unchanged, prefixed with one line when a required
extension is not ready:

> The `ilastik` extension is not installed; tools using it will refuse until the
> user installs it from Microclaw's Extensions panel.

Information, not a gate — design/61 settled that a predicate withholding a
Markdown file protects nothing. The catalog line stays visible either way, so
the model can still route to the skill and explain what is missing.

### Where the user text comes from

`microclaw/extensions.py`:

```python
EXTENSIONS = {
    "ilastik": "Read and run ilastik pixel-classification projects (.ilp).",
}
```

One line per extension, and nothing else in the module's data. `serve` and
`test` are absent because they are not user-facing, which is a decision rather
than a name-based exclusion rule. The panel derives everything else: the
packages from `Requires-Dist`, and the skills that need it from the catalog.

## What we deliberately do not build

**No tool that installs.** The agent gets no `install_extension`. Package
resolution during a session is network, minutes, and a chance to move a
dependency under a live acquisition; and routing it through the confirmation
gate would add a blocking prompt, which CLAUDE.md reserves for the user's own
agreement to ship. The refusal messages in `ilastik_adapter.py` change to point
at the panel, and that is the whole agent-side change. If sessions are later
measured stalling on this, that measurement buys the tool, and it bolts onto
this shape unchanged.

**No first-party package specs outside `pyproject.toml`.** See the correction
above. Community package locks belong to the community artifact and are never
merged into MicroClaw's project metadata.

**No new slot, no restart, no second staging path.** An extension is added to
the running environment; the update system stays the only thing that builds
slots.

**No version pinning UI, no uninstall.** Removing an extension is
`install.bat` plus not reinstalling it, which the reconcile already surfaces.
Add uninstall when someone asks for it.

**No `EXTENSIONS` validation layer.** It is a description table, not a registry
with predicates: one test asserts its keys are a subset of `Provides-Extra`, and
one asserts every skill's `requires` names one of its keys.

## Rejected alternatives

**Install into the inactive slot and restart.** Reuses design/58 verbatim and
has no in-place hazard at all — but it costs a full environment rebuild and a
restart to add one wheel, and it does nothing on an unmanaged install. Keep it
in reserve for the constraint-conflict case if that ever turns out to be common;
it is not the default.

**Requirements in an in-tree `SKILL.md`.** The plan's original shape. Loses the
allowlist, loses resolution, and puts the strings that reach uv's argv in a
document. This does not reject dependencies in a signed community package
manifest/lock installed into an isolated environment; that is a different trust
and execution boundary.

**Derive the panel text instead of `EXTENSIONS`.** "ilastik — installs h5py" is
free and needs no table. Rejected on the project's own ease-of-use rule: the
sentence a user reads before spending network and disk should say what the
extension is *for*.

**Teach `install.bat` to read `extensions.json`.** JSON in batch, for a case the
startup reconcile already covers with a button.

---

## Blocks

Blocks 70a–70c below implement the first-party extension path. The community
registry, package format, isolation protocol, and shared CLI/GUI installer need
a separate design and blocks before accepting external skills. They are not a
small addition to 70a: making them one would encourage the unsafe interim state
where community dependencies are installed into MicroClaw's live environment.

Two rig trips, both the demo machine. Nothing here needs a microscope: no
motion, no dose, no bridge. M2/M5/Nikon are not involved.

### 70a — the extension model, the endpoints, and the panel

**Items**

1. `microclaw/extensions.py`: `EXTENSIONS`; `available()` (name, description,
   requirement strings, packages, requiring skills, ready, recorded);
   `requirements_for(name)` reading `Requires-Dist` and refusing non-`extra`
   markers; `ready(name)` by dist-presence **and** import; `record`/`forget`
   against `user_data_dir()/extensions.json`.
2. `updates.locate_uv()` beside `locate_git()`, used by `stage_cached_candidate`
   too. Lift `write_state`'s atomic replace into `_write_json_atomic`.
3. `install(name)`: freeze → constrain → install → `invalidate_caches()` →
   import → record. Every failure returns a sentence naming the cause, and a
   failure whose cause could be an index override **names the variable**
   (`UV_INDEX_URL`, `UV_DEFAULT_INDEX`, `UV_EXTRA_INDEX_URL`, `PIP_INDEX_URL`)
   rather than quoting a URL — 58e's lesson.
4. `GET /api/extensions`, `POST /api/extensions/install`. One job lock, shaped
   like `update_job`. Refuse (409) while a turn holds `session.lock`, while an
   acquisition ledger is in flight, while a confirmation is pending, or while
   update staging runs. Rate-limit the install route with `_RateLimiter`.
5. Panel in `serve.html` with a pure `Transcript.extensionsView(state)` beside
   `updateBannerView`, so the rendering is testable without a browser.
6. `ilastik_adapter.py`'s two messages point at the panel.

**Tests** — the ones that must be watched failing on the pre-change tree are
marked ✦; the rest are regression or structure tests (mutate, don't watch).

- ✦ An install request for a name absent from `Provides-Extra` is refused, and
  **no uv subprocess is spawned** — assert on the spawn, not just the status.
- ✦ The argv passed to uv contains only strings drawn from `Requires-Dist`;
  a request body carrying `"ilastik; rm -rf /"` or `"h5py"` never reaches it.
- ✦ `--constraint` is present and the file pins every currently-installed
  distribution except `microclaw` and any direct-URL line.
- ✦ Readiness is false when the distribution is present but its module raises on
  import (fake a module that raises `ValueError`, which is h5py's real numpy-
  mismatch failure, not `ImportError`).
- An extra whose requirement carries `; python_version < "3.13"` is refused.
- 409 for each of the four in-flight conditions, parameterized over them.
- `extensions.json` survives a concurrent `write_state` (the reason it is its
  own file) — assert both files after interleaved writes.
- `Transcript.extensionsView` renders installed / not installed / recorded-but-
  missing / install-failed, from fixtures.

**The fake trap.** The uv fake must be written from uv's actual behaviour, not
from our caller: `uv pip freeze` output shape, a resolution *conflict* exit
(nonzero, message on stderr), and the fact that `uv pip` is a uv subcommand and
a plain `uv venv` has no pip module. Capture one real `uv pip install --dry-run`
transcript on the dev machine and paste it into the parse test. *A fake that
encodes your assumption is not a test of it.*

**Gate — demo machine, a program** (`design/70-block70a-demo-gate.py`, invoked
by a `.ps1` that resolves the active slot's interpreter from `active-slot.txt`,
as 58e's does). Independent limbs, each reported on its own, nonzero exit on any
failure, its own log file. A limb that could not run its mechanism reports
**NOT EXERCISED**, never a pass. Run `design/55-gate-probe-selftest.py` against
it on both trees before pushing.

Limbs: panel lists `ilastik` as not installed; install succeeds and the response
names the packages added; `h5py` imports **in the already-running server
process** (proved by a request that exercises the ilastik adapter, not by a
fresh subprocess — the no-restart claim is the whole point); `extensions.json`
records it; a second install of the same extension is a no-op that reports
"already installed"; the refusal limb — a POST with a name not in the allowlist
returns 400 **and** the log shows no uv invocation; the in-flight limb — POST
during a turn returns 409. Carry a **control that fires**: a deliberately
impossible extra name must fail the limb if the endpoint accepts it, so the
refusal limb cannot pass by doing nothing.

### 70b — carry extensions across an update, a rollback, and a reinstall

**Items**

1. `stage_inactive_slot` builds `f"{source}[serve,<recorded extras>]"`, falls
   back to `[serve]` on failure, records `extras_error`, and stages either way.
2. Startup reconcile: recorded-but-not-ready is surfaced in the panel with a
   Reinstall button. Local check only; no network, no automatic install.
3. `update_status()` gains nothing — the extras error belongs to the extensions
   panel, not the update banner. Two banners, one subject each.

**Tests**

- ✦ With `ilastik` recorded, the staged install spec is `[serve,ilastik]`.
- ✦ A failing extras install still publishes `pending-slot.txt` and still passes
  the smoke check; `extras_error` is recorded. (Watch this fail by making the
  fake uv fail only on the extras spec.)
- The smoke check is unchanged — it must not import an extension.
- Reconcile reports missing-after-rollback from a fixture where the record names
  an extension the running slot cannot import.

**Gate — demo machine, one program with 70c's limb appended.** A real update
cycle: install `ilastik` from the panel, stage an update, restart, then assert
the extension is *still* ready in the new slot and that `microclaw-slot.json`
names the new commit. Then `install.bat`, and assert the panel reports the
extension as missing with a working Reinstall — the recovery path, tested
against the state that makes recovery necessary. Score it from
`extensions.json`, `update-state.json` and the slot markers, not from the
banner text.

### 70c — a skill declares its extension

**Items**

1. `_parse_skill` accepts an optional `requires` list; `SkillMetadata` carries
   it. Malformed shape raises as the other frontmatter errors do; unknown extra
   names do **not** raise at import.
2. `load_skill` prefixes the one informational line when a required extension is
   not ready.
3. `test_skills.py`: every `requires` name is a key of `EXTENSIONS`, and every
   `EXTENSIONS` key is a `Provides-Extra` of the built distribution — the second
   asserted against the built wheel, alongside
   `test_built_wheel_contains_the_source_tree_skill_catalog`.

No skill in the tree declares `requires` yet, so **write a fixture skill that
does** — the shape of the input is the whole point, and a branch no fixture
reaches is a branch that ships unexecuted (58a's `github:` guard).

**Gate**: one limb on 70b's program — `load_skill` on a fixture skill naming an
uninstalled extension returns the body *plus* the line, and the same call after
installing returns the body alone.

## Open questions for the user

1. **Is `ilastik` the only extension at v1?** The design supports more the day
   an extra and a line in `EXTENSIONS` land together, but 70a's gate should
   exercise exactly what ships.
2. **Should a recorded-but-missing extension after an update be reinstalled
   automatically?** This plan says no — network on startup, without a button.
   Say so if you want it automatic; it is a two-line change and a different
   gate limb.
3. **What is the initial community artifact transport?** The recommendation is
   publisher-owned GitHub Releases plus a small reviewed registry of signed
   pointers, then move artifacts behind a dedicated service only if scale or
   availability requires it. A copied-skills repository is explicitly not the
   recommendation.
4. **What may a community skill execute in v1?** The safest useful first cut is
   Markdown/assets only. Executable packages should wait for the out-of-process
   protocol, capability grants, signing policy, and revocation story rather than
   inherit the current process's filesystem and hardware authority.

## Run ledger

| Block | Branch | Start commit | Status |
|-------|--------|--------------|--------|
| 70a | — | — | not started |
| 70b | — | — | not started |
| 70c | — | — | not started |
