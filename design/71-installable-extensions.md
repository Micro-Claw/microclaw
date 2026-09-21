# Installable extensions — optional dependencies a user can actually install

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

## Verdict

**The first-party half is viable, and the mechanism was measured working before
this was written.** The allowlist really is in the built metadata:
`importlib.metadata.metadata("microclaw")` on this checkout returns
`Provides-Extra: ['ilastik', 'serve', 'test']` and
`Requires-Dist: 'h5py>=3.10; extra == "ilastik"'`, so an endpoint that takes an
extra name and reads its own `Requires-Dist` never sees a package name from the
network. Three sections below were corrected by measurement rather than review.

**The community-package half is viable, but is not part of this notebook's
blocks.** It is a separate product boundary and therefore opens as its own
design notebook rather than growing 71a–71c. The findings and settled decisions
that notebook starts from are recorded under *Community skill packages* below.
SMAPpy is its first conformance package. Publisher work is a gate dependency,
not a reason to defer designing MicroClaw's generic half.

**The correction for first-party extensions.** The plan's stated goal 1 — *"it
keeps project dependencies out of our pyproject.toml"* — is the wrong target,
and taking it literally removes the property that makes the feature safe.
Extras belong **in** `pyproject.toml`; what they stay out of is the *default*
dependency list, which is where the tax actually is. `pyproject.toml` is what
gives an extension a resolvable version range, a review in the block workflow,
and — decisively — a place in the built wheel's own metadata. Requirement
strings that live in a `SKILL.md` instead are strings a contributor can change
without touching packaging, and there is then no allowlist: the install endpoint
would be taking package names from a document rather than from the distribution
it is running.

So: **the unit of installation is a pyproject extra, and the allowlist is the
running distribution's own `Provides-Extra` metadata.** A skill *names* an extra;
it never names a package. Both halves are still committed to the repository, so
the plan's vetting property holds — it just holds through packaging metadata
instead of through free text. Goal 2 (a user cannot find the venv) is fully
served either way, and it is the goal that matters.

**The narrowing.** Do not reuse the *slot* half of design/58. Staging rebuilds
the inactive environment and requires a restart; that is right for replacing
Microclaw and wrong for adding `h5py`, which must be usable in the session that
asked for it. Reuse the smaller pieces — uv discovery, atomic state next to
`update-state.json`, the job lock and banner pattern, "prove it by running it" —
and install into the environment that is running.

The panel needs four states and not the six an external catalog would need: **not
installed**, **installing** (the job lock is held), **ready** (recorded and
importable), and **install failed** or **recorded but missing** — both durable,
both carrying a sentence and a button. There is no *Available* state, because
the catalog is the running wheel's own metadata and cannot be unreachable.

## Measured, on this checkout, 2026-09-02

Everything below was run against `.venv` (uv, Python 3.12.14) before the design
was accepted. Two of the three findings contradict what the earlier draft said.

**1. The freeze exclusion rule as written produces a constraint file uv
refuses.** `uv pip freeze` renders an editable install as a line with no
distribution name at all:

```text
-e file:///Users/zachcm/Code/microclaw
```

Dropping "`microclaw`" by name and "`@ ` direct URL" lines — the rule the draft
gave — leaves that line in place, and uv then rejects the **whole file**:

```text
error: Couldn't parse requirement in `constraints.txt` at position 0
  Caused by: Expected one of `@`, `(`, `<`, `=`, `>`, `~`, `!`, `;`, found `P`
```

So the install would have failed on every developer checkout — which is exactly
the unmanaged install this design claims "works identically". Do not parse
freeze output at all. Build `name==version` pins from
`importlib.metadata.distributions()`, which exposes the installed distribution
name and version even when its origin is editable or a direct URL. Canonicalise
names, exclude MicroClaw itself, and refuse conflicting duplicate versions.
Nothing is dropped, so every installed distribution the resolver could move is
constrained. *An enumeration that fails must say so; an empty list is a
statement, not a silence.*

**2. The constraint file bounds versions; it does not forbid change.** The draft
called it "a structural guard". Half of that is confirmed: with every dist
pinned at its installed version, a requirement needing more fails outright —

```text
uv pip install --constraint c.txt 'numpy>=2.6'
  × No solution found when resolving dependencies:
  ╰─▶ Because you require numpy>=2.6 and numpy==2.5.2, we can conclude that
      your requirements are unsatisfiable.
```

— and that is the hazard the feature must not have. But a constraint that names
a version *other* than the installed one is an instruction, not a veto. With
one line edited to `numpy==1.26.0`, uv reported `Would uninstall 1 package /
Would install 1 package`, downgrading the live environment's numpy. The guard is
therefore not the `--constraint` flag; it is the constraint file being derived
from **this** environment inside **this** call. Enumerate installed metadata
immediately before installing, in the same function, and never cache it, never
write it beside `extensions.json`, never carry it across a slot activation.
*A cache computed against the previous install is not evidence about this one*
applies here literally, and the failure it buys is a downgrade under a live
acquisition.

**3. Readiness by import is real, but it is a property of the *consumer*, not of
the extension.** `ilastik_adapter.py` imports `h5py` inside the two methods that
use it (`:368`, `:421`), and catches `(ImportError, ValueError)` at both — so the
already-running server really can use a freshly installed `h5py` with no
restart, and a failed import leaves no negative `sys.modules` entry to defeat
the retry. That is a fact about this adapter. An extension whose consumer
imports at module scope would be reported ready by an installer that imports in
its own frame while the consumer kept failing until a restart. State the rule
with the feature: **a module that backs an extension imports its dependency
inside the function that needs it**, and a test asserts that for every extension
in `EXTENSIONS`.

**4. `distributions()` enumerates the *calling process's* `sys.path`, so it is
CWD-sensitive — and `purelib` alone is not the target environment.** The
revision above is right that metadata beats freeze text, but it changed the
vantage point: `uv pip freeze --python <target>` asked uv about the environment
being installed into, while in-process enumeration reports whatever *this*
interpreter can see. Measured, same interpreter, three runs:

| Where it ran | `len(list(distributions()))` |
|---|---|
| repo root | **64** |
| `/tmp` | 63 |
| explicit `path=[purelib]` context | 63 |

The extra entry from the repo root is the source tree's `microclaw.egg-info`,
found because the CWD is on `sys.path`; `microclaw` therefore appears twice in
every developer checkout. Two consequences, both measured:

- **A stray `*.egg-info`/`*.dist-info` in the CWD becomes a pin.** A planted
  `foo.egg-info` naming `foo 9.9.9` produced the pin `foo==9.9.9` for a
  distribution not installed in the target at all. Harmless until something
  resolves `foo`, and then it fails for a reason that has nothing to do with the
  environment.
- **The dangerous version of that is already caught.** A planted
  `numpy.egg-info` naming `1.26.0` beside the installed `2.5.2` does *not*
  silently become measurement 2's downgrade instruction — the conflicting-
  duplicate check fires (`CONFLICT REFUSAL FIRES: numpy 1.26.0 2.5.2`). That
  refusal is load-bearing, not defensive tidiness.

Restricting discovery to `purelib` removes the CWD entry but also removes valid
parts of an environment: `platlib` can differ, and an editable install can be
visible through a `.pth`/source path rather than live beneath either directory.
That would leave a distribution the target resolver sees unconstrained. Ask the
**target interpreter** to enumerate its effective path instead: invoke it with
fixed helper code in isolated mode (`-I`) from an empty temporary CWD. Isolated
mode removes CWD, `PYTHONPATH` and user-site accidents while normal site startup
still processes the target environment's `.pth` files. The helper returns only
name, version and a diagnostic origin obtained with public
`dist.locate_file("")`; the caller validates and pins them. The invariant is
structural again: the interpreter that enumerates is the interpreter
`--python` targets. *An editable install resolving to the wrong tree* is a trap
this project has already been bitten by once.

Each of those three claims was then measured rather than left asserted, and the
`purelib` rejection is the one that matters most — it was this document's own
previous recommendation:

- **`purelib` really does drop a real distribution.** A legacy develop install
  (a `.pth` in site-packages naming a source tree that holds `bar.egg-info`) is
  enumerated by the subprocess as `bar 3.3.3` and is **invisible** to a
  `path=[purelib]` context. It would have been the one distribution left
  unpinned — measurement 1's failure, reintroduced by the fix for it.
- **`-I` is the flag doing the work, not the empty CWD.** With `-I` alone, from
  a directory holding a planted `foo.egg-info`: 63 distributions, no `foo`, and
  `sys.path[0]` is the stdlib zip rather than `''`. Without `-I`, same
  directory: 64 and `foo==9.9.9`. `-I` additionally ignores `PYTHONPATH`, which
  an empty CWD does not — `PYTHONPATH=<dir holding foo.egg-info>` yielded `foo`
  **twice** without `-I` and nothing with it. Keep the empty CWD if you like,
  but the comment must not credit the two equally: a later simplification that
  drops `-I` and keeps the temp directory silently loses `PYTHONPATH`
  isolation.
- **Normal site startup does still process `.pth` files under `-I`.** That is
  what makes the first bullet work — and it is also the next measurement.

**5. A `.pth` file can print to stdout before the payload, so the stream must
not *be* the payload.** `.pth` files execute `import` lines during `site`
startup, which `-I` does not disable (only `-S` would, and `-S` would remove
site-packages discovery altogether). Any third-party `.pth` whose import prints
a banner lands ahead of the JSON:

```text
some vendor banner nobody asked for
[{"name": "bar"}]
```

```text
JSON DECODE FAILED: Expecting value: line 1 column 1 (char 0)
```

Every install would then refuse, on a healthy environment, for a reason naming
nothing an operator could act on. This is 58e's lesson arriving through a
different door — there it was our own CLI's `Press Enter to close this
window...` after its JSON; here it is any `.pth` in the *target* environment,
and `distutils-precedence.pth` and `__editable__*.pth` are ordinary furniture.
*Parse the payload out of the stream; never require the stream to be the
payload.* Have the helper write its JSON to a path passed in argv and ignore
stdout entirely, or delimit the payload with a sentinel the caller scans for.
The measurement was not hypothetical: it broke this session's own test harness
first, when a `$(...)` capture of the interpreter's `purelib` came back with the
banner glued to the front of the path.

Two smaller measurements. `importlib.metadata.packages_distributions()` is keyed
by module and valued by distribution, so the inversion the readiness check needs
must normalise names (`pytest-mock` provides `pytest_mock`); and extracting
`h5py` from `h5py>=3.10` wants `packaging.requirements.Requirement`, which is
present on every rig only because `scikit-image` requires it and **microclaw does
not declare it**. Declare `packaging` in `[project].dependencies` — it is already
installed everywhere, so it costs nothing — or the installer rests on a
transitive dependency nobody promised.

## Rechecked 2026-09-18, against a tree that has moved

Every code anchor this notebook names still matches except the exporter's, and
every measured mechanism reproduces. `skills.py:48` / `:101`, `updates.py:447`
and its failure branch, `serve.html:242`, `transcript.js:282` and
`ilastik_adapter.py:368`/`:421` are unchanged. 71c needs no adjustment; 71b's
reinstall gate does: `install.bat` reuses a working environment, so an ordinary
reinstall preserves installed extras. Its missing-extension limb must arrange
a rebuilt environment explicitly.
Measurement 1 reproduces on uv 0.12.8 (the editable still freezes as a nameless
`-e file://` line) and measurement 4's mechanism reproduces exactly — only its
absolute counts moved, which is why the test bullet below now asserts the
difference rather than either number. `packaging` is still undeclared and still
reaches every environment through `scikit-image`'s `packaging>=21`.

The exporter anchors in the community brief were re-pinned here: `refuse()` is
`tools.py:2284`, its `# NOT EMITTED` + `raise RuntimeError` pair `:2297-2298`,
the `renderer is None` branch `:2316-2321`, the `@emits_nothing` branch
`:2313-2315`, the recorded-pairs loop `:2300`, and `_recorded_outcome` `:1040`.
The behaviour at each is unchanged, so R84's conclusion stands; only the numbers
had rotted.

**The one new constraint is CI** (design/58-P, 2026-09-16).
`.github/workflows/tests.yml` runs the suite on `ubuntu-latest` and
`windows-latest` for every pull request, installing `.[serve,test,ilastik]`
with **pip**. Three consequences for 71a:

- **`h5py` is always installed there**, so a test shaped *not installed → install
  → ready* asserts the machine it runs on. Readiness tests drive fixtures; only
  the demo gate observes a genuinely absent extension. Two commits on 2026-09-16
  cleaned up exactly this defect elsewhere in the suite, and this block is the
  most likely place to reintroduce it.
- **The workflow does not provision `uv`**; using pip does not establish that
  the runner image lacks it. CI's uv invocations use fakes, and discovery tests
  explicitly control presence and absence rather than inspecting the host.
  Real-uv evidence comes from the development measurements and demo gate.
- **`locate_uv()`'s `%USERPROFILE%\.local\bin\uv.exe` route is Windows-only.**
  Its test must not assume either platform's filesystem; the PATH route is the
  part both runners can exercise.

**6. Measured: what uv prints when nothing is a terminal.** The progress design
below rests on this, so it was run rather than assumed — uv 0.12.8, into
throwaway venvs, output redirected. Uncached:

```text
Using Python 3.12.14 environment at: uvprobe2
Resolved 2 packages in 242ms
Downloading numpy (5.2MiB)
Downloading h5py (2.9MiB)
 Downloaded h5py
 Downloaded numpy
Prepared 2 packages in 369ms
Installed 2 packages in 12ms
 + h5py==3.16.0
 + numpy==2.5.3
```

Four facts, each load-bearing below:

- **It streams.** `Resolved ... in 242ms` arrived ~239 ms after launch, not at
  exit; uv's self-reported duration matching its arrival time is the evidence.
  (Per-line timestamps carry the instrument's own overhead, so the gaps between
  lines are not quotable — the ordering is.)
- **All of it is stderr. stdout is empty.** uv's failure text is the same
  stream: 71a item 3's "a resolution conflict says which requirement fought
  which pin" is read from it, and so is the progress. One reader, fanning out
  to the phase and to the retained error tail — never two consumers of one
  pipe. (The bounded tail in *Pin the current environment* is the
  `ENUMERATE_DISTRIBUTIONS` helper's, a different subprocess.) `--dry-run` is
  on stderr too.
- **`capture_output=True` buffers until exit**, so publishing progress while uv
  runs means `Popen` and a reader thread rather than `subprocess.run`. Keep
  `stdin=subprocess.DEVNULL` through that change — 58e's inherited console is
  exactly the failure a hand-rolled `Popen` reintroduces.
- **The vocabulary is literal and per package.** `Resolved` ends resolution;
  `Downloading`/`Downloaded` are emitted **per package**, so there is no
  whole-operation percentage to render; there is no `installing` line at all —
  uv prints `Prepared` then `Installed`. `checking environment` is ours, which
  is fine because we own that phase.

A **warm-cache** install printed no `Downloading` lines at all and finished in
41 ms. That is the ordinary repeat case, and it is why the gate limb's NOT
EXERCISED clause is right. Even the demo machine's first `ilastik` install may
use cached packages: absence from the active environment does not imply absence
from uv's cache. An uncached install offers more opportunity to observe progress;
if no transition is observed, the limb remains NOT EXERCISED.

**R131 is this block's shape, found after it was written.** A staging build sits
on `Building the update…` with no phase and no bound, and `stage_inactive_slot`
passes no timeout to uv; an operator read a healthy build as hung. 71a adds a
second long uv subprocess behind a second banner with a job lock *shaped like*
`update_job`, so it reproduces R131 unless the **installing** state carries the
phase uv is in. It should. A bound is optional and takes R131's own caution: a
timeout that fires on a slow machine is worse than none, so generous or
self-calibrated, never a constant.

## Decision

### First-party extras here; community packages open in a separate notebook

This notebook covers code MicroClaw owns and imports in its own process:
`ilastik` today, and any later integration that extends MicroClaw itself. Those
must be reviewed and version-constrained *with* MicroClaw, which is what a
pyproject extra does.

It does **not** put accepting community skills into blocks 71a–71c. That needs a
package boundary so the author owns their releases and pins, and it is a
different product with different failure modes; the work done on it is
preserved under *Community skill packages* below as the brief for its own
notebook. Nothing in 71a–71c may grow toward it: no requirement string from a
document, no per-release environment, no out-of-process runner.

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
                       --constraint <fresh installed-metadata pins>
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

Enumerate first, constrain second, **in one call**:

```python
# Fixed helper code runs under the same `python` later passed to uv. `-I` is
# what excludes caller CWD, PYTHONPATH and user-site metadata (measurement 4);
# the empty CWD is belt-and-braces and must never be mistaken for the guard.
# Normal site startup still runs, so .pth-based editables stay visible -- and
# so does any .pth banner on stdout, which is why the payload is a file and
# stdout is not parsed (measurement 5).
records = run_json_file(
    [python, "-I", "-c", ENUMERATE_DISTRIBUTIONS, payload_path],
    payload_path, cwd=empty_temp_dir,
)
versions, origins = {}, {}
for record in records:
    raw_name = record.get("name")
    version = record.get("version")
    origin = record.get("origin")
    if not raw_name or not version:
        raise ExtensionInstallError(
            f"an installed distribution has no usable identity: {origin}"
        )
    name = canonicalize_name(raw_name)
    if name == "microclaw":
        continue
    if name in versions and versions[name] != version:
        raise ExtensionInstallError(
            f"conflicting installed metadata for {name}: {versions[name]} at "
            f"{origins[name]} and {version} at {origin}; remove the stale one"
        )
    versions[name], origins[name] = version, origin
pins = [f"{name}=={version}" for name, version in sorted(versions.items())]
install = uv("pip", "install", "--python", python,
             "--constraint", constraints, *requirements)
```

`ENUMERATE_DISTRIBUTIONS` contains no request or catalog data. It iterates
`importlib.metadata.distributions()` and JSON-encodes
`{"name": dist.metadata.get("Name"), "version": dist.version,
"origin": str(dist.locate_file(""))}` **to the path given as its one argument**.
The subprocess has a timeout and bounded stdout/stderr tails for diagnostics,
but the payload is read from that file, never from the stream — measurement 5,
where a `.pth` banner ahead of the JSON refused a healthy environment. A missing
or unparseable file, or a nonzero exit, refuses before uv is spawned. Do not use
private `dist._path`, whose presence and shape are implementation details.

The refusal message names the **origins**, not just the two versions, because a
conflicting duplicate is stale metadata someone has to delete and "1.26.0 and
2.5.2" tells an operator nothing about where to look. But `locate_file("")`
returns the *containing directory*, not the metadata directory, and the
commonest stale shape puts both entries in one place: two `baz-*.dist-info`
directories in a single site-packages report the **same** origin for 1.26.0 and
2.5.2 (measured). So word it as two metadata entries for `baz` found in that
directory, with both versions — enough to `ls` and delete — and do not promise
two distinct paths the public API cannot supply.

Measurement 1 above is why freeze text is not the source: origin syntax is not
installed identity, and dropping an unparseable line would leave exactly the
distribution that needs constraining unpinned. Measurement 2 is why metadata is
enumerated here and never persisted: with the pins equal to what is installed,
a requirement that needs a newer version fails resolution and reports a
conflict instead of performing one, but a *stale* pin is an instruction to
downgrade. Measurement 4 is why the target interpreter performs discovery from
an isolated CWD; the conflict check remains load-bearing for genuinely
conflicting metadata visible inside that environment. The guard is complete,
fresh installed metadata for **the target interpreter**, not the flag.

This is also what makes the in-place install *possible* on Windows and not
merely safe: nothing already imported is replaced, so only new files are written
into the running interpreter's `site-packages`.

Do not run a dry-run preview (operator decision, 2026-09-18). Before pressing
Install, the panel discloses the packages from the wheel's `Requires-Dist`.
The real install reports added packages and exact versions in its `+ name==version`
lines. A dry-run inside installation adds another resolution and failure point
only to repeat information the real install reports immediately afterwards;
there is no pre-button resolution endpoint in this design.

### Prove the extension by importing it

uv exiting 0 says a wheel was unpacked. `h5py` with a mismatched HDF5 or numpy
raises on import — which is why `ilastik_adapter.py` catches
`(ImportError, ValueError)` and not `ImportError` alone. *A marker's existence is
not health* applies unchanged: readiness is

1. every distribution named by the extra's requirements is present, and
2. every top-level module those distributions provide
   (`importlib.metadata.packages_distributions()`, inverted with a canonicalised
   name — derived, not tabulated) imports.

After a successful install, call `importlib.invalidate_caches()` and run that
import **in this process**. That is both the proof and the thing the user
wanted: the session that asked can now use it, with no restart. A readiness
failure after a successful uv run is reported as such — installed, not usable —
and names the exception.

The no-restart claim is only true while the *consumer* imports lazily
(measurement 3). `ready()` importing in the installer's own frame is not
evidence that the code which needs the extension can now import it: a
module-scope importer that already failed keeps failing until the process
restarts, and the panel would say ready. So a test asserts, for every key of
`EXTENSIONS`, that no module in `microclaw/` imports that extra's top-level
modules at module scope — and the demo gate proves it the only way that counts,
by exercising the ilastik adapter through a request to the **already-running
server** rather than a fresh subprocess.

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

`stage_inactive_slot` installs `f"{source}[serve]"` (`updates.py:447`). With
extensions recorded it installs `f"{source}[serve,ilastik]"` — otherwise every
update silently removes the extension, which is *"a cache computed against the
previous install"* in a new costume.

**An extension must never be able to break an update.** If the combined spec
fails, retry with `[serve]` alone; if that succeeds, stage it and record the
extras failure for the panel. The smoke check stays exactly as it is — it proves
`microclaw`, `microclaw.webserve` and `uvicorn`, never an extension.

**That retry cannot go through the existing failure branch.** The command loop
in `stage_inactive_slot` writes `build_error`, `build_failed_commit` and
`build_error_detail` and raises `UpdateError` on the first nonzero exit, and the
top of the same function then refuses that SHA until `next_check` passes. A
fallback bolted on after the loop would poison the interval for a commit that
builds perfectly well without the extra — *a record that some earlier attempt
failed is not a record of this one*, and *clearing one of two poison keys is
clearing neither*. Structure it so the extras attempt is tried and discarded
**before** any state is written, and let only a `[serve]` failure reach the
existing branch. The success path already pops all three keys, so a fallback
that succeeds must run to the end of the function rather than return early.

Three states can leave a recorded extension unusable: a rollback to a slot
without it, an installer rebuild of the environment, and a staged extras
failure. An ordinary `install.bat` reinstall reuses a working environment;
installing `[serve]` does not remove already-installed extras. A rebuilt
environment starts without them, and the installer is deliberately *not* being
taught to read JSON. So at startup, compare the record against readiness and, when
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

`_parse_skill` currently asserts `set(metadata) != {"name", "description"}`
(`skills.py:48`), an exact-set equality that rejects any third key. That becomes
an allowed-key check: `name` and `description` required, `requires` optional (a
list of nonempty strings), anything else still an error. It does **not** check
the names against installed metadata — a malformed contributor edit must not
make `SKILL_CATALOG` raise at import time and take the whole application down,
and `SKILL_CATALOG` is built at module import (`skills.py:101`). That check is a
test.

`load_skill_text` — the function; `load_skill` is the tool that wraps it — reads
the resource and returns the **whole file, frontmatter included**. So the
informational line is prepended above the opening `---`, not "before the body",
and nothing downstream re-parses that text. One line when a required extension
is not ready:

> The `ilastik` extension is not installed; tools using it will refuse until the
> user installs it from Microclaw's Extensions panel.

Information, not a gate — design/61 settled that a predicate withholding a
Markdown file protects nothing. The catalog line stays visible either way, so
the model can still route to the skill and explain what is missing. `load_skill`
stays `@emits_nothing`, which is already correct and needs no change.

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

**No first-party package specs outside `pyproject.toml`.** See the verdict.
Requirement strings that reach uv's argv come from built metadata or the feature
has no allowlist at all.

**No community package mechanism, no catalog intake, no out-of-process worker,
no acquisition trigger in blocks 71a–71c.** These open in their own notebook,
for the reasons below. A block here that starts down that road is out of scope
even if it looks small.

**No new registered tool.** Nothing in 71a–71c adds one, so nothing here needs
an `@emits` decision: `load_skill` is already `@emits_nothing` and keeps that
marker, and the two endpoints are HTTP, not tools. The community notebook has
the separate, explicit export disposition below.

**No new slot, no restart, no second staging path.** An extension is added to
the running environment; the update system stays the only thing that builds
slots.

**No version pinning UI, no uninstall.** Note what the `install.bat`
correction above costs here: a reinstall *preserves* an extension, so there is
no user-facing way to remove one short of forcing an environment rebuild. The
decision stands — nobody has asked, and `uv pip uninstall` of a shared
transitive dependency is a worse hazard than keeping an unused wheel — but it
is now a real gap rather than something the reconcile surfaces. Add uninstall
when someone asks for it, and say this much in the panel if they do.

**No `EXTENSIONS` validation layer.** It is a description table, not a registry
with predicates: one test asserts its keys are a subset of `Provides-Extra`, and
one asserts every skill's `requires` names one of its keys.

## Rejected alternatives

**Install into the inactive slot and restart.** Reuses design/58 verbatim and
has no in-place hazard at all — but it costs a full environment rebuild and a
restart to add one wheel, and it does nothing on an unmanaged install. Keep it
in reserve for the constraint-conflict case, which is now a *measured* outcome
rather than a hypothetical one (measurement 2: a requirement above a pin is a
clean `No solution found`, and the response has to say something useful when it
happens). It is not the default, and `ilastik`'s only requirement is `h5py`,
whose numpy floor this environment already clears.

**Requirements in an in-tree `SKILL.md`.** The plan's original shape. Loses the
allowlist, loses resolution, and puts the strings that reach uv's argv in a
document. It also does not survive contact with uv: the strings would have to
be turned into argv without ever having been resolved against anything.

**Derive the panel text instead of `EXTENSIONS`.** "ilastik — installs h5py" is
free and needs no table. Rejected on the project's own ease-of-use rule: the
sentence a user reads before spending network and disk should say what the
extension is *for*.

**Teach `install.bat` to read `extensions.json`.** JSON in batch, for a case the
startup reconcile already covers with a button.

---

## Community skill packages — next notebook brief

The community half of the original proposal is right about the ownership
problem and wrong to be five blocks of this notebook. It is recorded here as
settled decisions, findings and gate dependencies; it should open as its own
design notebook, with SMAPpy as the first conformance package, rather than wait
for a second use case.

### Why it is not in this notebook

**It is a different product, not a later block.** 71a–71c are one module, two
endpoints, one panel and a frontmatter key. The community mechanism is a package
format, a catalog intake with a trust policy, a cached verified catalog, a per-release
environment manager with staging/activation/rollback/repair/remove, a process
supervisor, a versioned job protocol, an acquisition trigger with persisted
receipts, and a new agent tool. Sharing an "atomic state primitive" with 71a is
not shared scope. *Don't add layers* and *keep design documents short* both cut
against carrying it here.

**Its export answer must be explicit, and the marker it needs is not
`@refuses`.** Every registered tool carries exactly one of `@emits` /
`@emits_nothing` / `@refuses`, enforced by
`test_every_registered_tool_has_exactly_one_export_decision`, and *a new
capability is not finished until it can appear in an exported script.* The
intent above is right — preserve the acquisition, disclose the analysis — but
neither of the two obvious markers produces it, and the code says so plainly:

- `@refuses` routes through the `renderer is None` branch
  (`tools.py:2316-2321`) into `refuse()` (`:2284`), which appends
  `# NOT EMITTED: …` **and** `raise RuntimeError(…)` on the next line. A session
  that ran the analysis tool therefore exports a script that halts there and
  strands every later step — including the `run_timelapse` the same session
  emitted correctly. That is exactly the shape block 43h
  (`generate_and_save_hook`), block 47 (`set_roi`/`clear_roi`) and block 52a
  (`move_named_stage`) each paid a gate for. The stated requirement that export
  "must never replace an otherwise-emittable `run_timelapse` with a total
  export failure" is violated by the marker, not by the acquisition emitter.
- `@emits_nothing` prints `# No hardware-routine effect.` and continues
  (`:2313-2315`), which is silence about an HDF5 the session really produced —
  and *silently deleting a step is worse than the raise it replaces*.

So the analysis tool takes **`@emits`**, with a renderer that emits a comment
block rather than executable code: package ID, release digest, operation,
reserved output directory, and one line saying the analysis was not reproduced
and how to rerun it. It emits, so the script continues; it discloses, so nothing
vanishes; and it needs no change to `refuse()`. A later portable
package-reference format can turn that comment into code, and v1 does not
fabricate one.

**The trigger's comment is unrenderable unless the acquisition records it.** The
export loop iterates recorded `(tool, params)` pairs (`:2300`). A trigger is
*configuration*, so it never appears in that list, and `_emit_acquisition` has
nothing to render a disclosure from. The trigger design must therefore make the
acquisition tool's **recorded result** carry the fired trigger's package,
release and operation — *an emitter may only render what the record contains*,
and this is the same debt the shutter, the EMU write and the centring loop each
had to pay in 63a before they could be emitted at all.

**And an analysis failure must not reach the record in the shape
`_recorded_outcome` reads.** That function (`:1040`) decides how much of a call
completed from exactly two structural signals: a **top-level `error`** key, and
per-item `error` entries inside a **top-level list**. A worker crash reported
either way turns a flawless acquisition into a `refuse()` (partial) or a skipped
no-op (nothing) at export time. "Analysis failure never aborts or slows frame
capture" needs its second clause: it is recorded under a nested key, never as a
top-level `error` and never as an entry in a top-level list. Pin all three of
these in exporter tests before writing the runner.

**A disk-following external worker is an acquisition observer, not an
acquisition hook.** The `CLAUDE.md` rule that image analysis lives in hooks
continues to govern MicroClaw-owned in-process analysis and any result that
participates in acquisition decisions. SMAPpy is different: third-party code
must not run inside the privileged hook runtime, and feeding a bounded fitting
queue from a frame callback can block acquisition when the fitter falls behind.
The allowed exception is narrow: an out-of-process, read-only observer follows
the growing collision-resolved dataset on disk, writes only its reserved output
directory, and has no channel to change, slow, abort or gate acquisition. If its
telemetry is later used for feedback, that feedback returns through a new typed
MicroClaw capability and the normal authorization/envelope/write-budget rules;
the package itself never gains hardware authority.

**Its real-package gate depends on a third party; its generic design does not.**
The publisher-owned changes listed below are preconditions for admitting
SMAPpy. MicroClaw can specify and test the package format, intake validation,
isolated installer, process protocol and lifecycle against a fixture package in
parallel. The SMAPpy conformance limb remains NOT EXERCISED until the publisher
supplies a conforming release; MicroClaw does not patch around it.

**The ownership win depends on intake, not hand-merging every release.** The
publisher owns and hosts the artifact, skill code, pins, wheel matrix and
release testing. MicroClaw owns admission policy and the catalog. The publisher
submits a signed, digest-pinned record to an intake channel; automated checks
verify publisher identity, schema, immutable artifact digest, supported wheel
matrix, hashed lock, protocol conformance and self-check. A passing record may
be promoted automatically under policy or held for lightweight approval. The
publisher never writes directly to the trusted production catalog. This means
we review the policy and exceptional releases, not maintain SMAPpy or manually
copy every ordinary release.

**One scoping fact found while checking it.** `run_mda` drives MMStudio's own
MDA and never touches `_acquire_with_hooks`, so an `on_dataset_created` trigger
would silently do nothing for MDA runs. Any trigger design must say so in the
panel rather than leave an operator watching for output that cannot arrive.

### What the boundary should be, when it is taken up

Recorded so the next notebook starts from decisions rather than a blank page,
and deliberately compressed. The ownership, hosting, intake, isolation,
observer and export dispositions below are settled; manifest schemas, protocol
fields, service implementation and block boundaries remain for that notebook.

The unit is an immutable publisher release carrying `SKILL.md`, assets, a
manifest (package ID, publisher, version, compatible MicroClaw/Python ranges,
entry points, license, source and issue URLs, digest) and a dependency lock per
platform. MicroClaw owns the protocol, installer, trust policy and catalog UI;
the publisher owns the skill and its pins.

Dependencies never enter MicroClaw's environment: one environment per release
under the user data directory, keyed by ID and digest, installed from the
publisher's lock with hashes required. Code runs out of process behind a
versioned protocol and receives data only — never a controller, guard, hook
object, token or server URL. A Markdown-only skill needs no environment. A
package wanting in-process imports or privileged APIs is a first-party
extension proposal and takes the extra path above.

MicroClaw does **not** operate an artifact repository. Transport starts as a
publisher-owned GitHub Release containing the skill artifact; its lock may
point at exact hashed PyPI wheels. The publisher pushes a signed submission to
MicroClaw's catalog intake, and the promoted catalog record pins the release
artifact's exact digest. A repository of copied skill files is explicitly not
the recommendation: it changes the queue without moving the ownership. An
unsigned floating URL or a bare package/version is never a release identity.

```text
SMAPpy CI -- publish --> publisher GitHub Release / PyPI wheels
    |
    +-- submit signed digest-pinned record --> MicroClaw catalog intake
                                                   |
                                      validate + conformance gate
                                                   |
                                                   v
                                        trusted GUI catalog
                                                   |
                                      isolated exact installation
```

The intake record contains metadata and immutable references, never arbitrary
shell text, dependency ranges to resolve freely, or permission to call hardware.
Promotion and later delisting affect discovery; they do not remotely delete an
installed release. The publisher may submit a yank/superseding release through
the same authenticated channel, while MicroClaw retains final catalog policy.

Installation is a durable transaction, and *Available* means only that the
trusted catalog has a compatible non-yanked release — never that this machine can
install it. Keep the previous verified release live until a candidate passes,
switch an `active` pointer atomically, retain one release for rollback; a
failure never leaves a partial skill, and a later verification failure keeps
the skill visible and disabled rather than silently absent.

Installing grants no right to run. A trigger is configured per workflow or rig
profile, pins release/operation/parameters/capabilities in a receipt, and can
be disabled without uninstalling. Analysis failure must never abort or slow
frame capture, the worker has no backpressure channel into acquisition, and
every acquisition exit — including design/60's unterminated one — sends a
terminal state and keeps any valid partial artifact.

### Feasibility against SMAPpy 0.1.0

Reviewed 2026-09-02 against
[SMAPpy commit 9238192](https://github.com/ries-lab/SMAPpy/commit/92381926515a8304b988679e13f4f8b3fae98740)
and the published
[`smappy-smlm` 0.1.0 release](https://pypi.org/project/smappy-smlm/0.1.0/).
The integration is feasible, but **the proposed MicroClaw patch is not the
integration boundary we will accept**:

| Proposed shape | Finding | Required disposition |
|---|---|---|
| Copy `integration/microclaw/skills/smappy/SKILL.md` into MicroClaw | This makes MicroClaw publish and test the author's operating instructions. The copied text can drift from the package version that implements it. | SMAPpy's release artifact carries the skill and manifest; MicroClaw only loads the installed, verified copy. |
| Copy `test_smappy_skill.py` into MicroClaw | Assertions over SMAPpy's prose and API names make our suite a compatibility bridge. | Those tests remain in SMAPpy. MicroClaw tests only the package protocol with a fake package and runs one black-box conformance gate against a real SMAPpy release. |
| Add `analysis = ["smappy-smlm>=0.1"]` to MicroClaw's `pyproject.toml` | This makes SMAPpy look first-party, resolves it into MicroClaw's live environment, and makes us choose its version range. | Install the exact publisher release and locked dependencies in the skill's environment. No `analysis` extra lands here. |
| Import `smappy` from skill prose | `load_skill` supplies instructions, not a Python execution capability; the agent has no safe arbitrary-Python tool. | The package declares a protocol entry point. MicroClaw invokes only that entry point with a typed job. |
| Start fitting automatically while acquisition writes | Technically sound: SMAPpy can follow a growing NDTiff index and the real collision-resolved dataset path exists after `Acquisition(...)` and before `acq.acquire(events)`. It is not accomplished by installing or loading the skill. | Add an explicit, persisted acquisition trigger and start the worker at the dataset-created boundary. |
| Feed frames through an `analyze_frame` hook and `QueueSource` | A bounded queue can block the acquisition thread when fitting falls behind, while an out-of-process queue needs a new image transport. It also couples the package to MicroClaw's privileged hook runtime. | v1 follows the growing NDTiff on disk. Queue/frame streaming is deferred until it has measured need and a non-blocking transport. |
| Open `live_view`/matplotlib | It needs a GUI main thread and can hang the server process. | v1 is headless and writes HDF5 plus optional PNG. A viewer, if offered later, is a separately supervised process and capability. |

The package itself is plausible for the rigs we care about: 0.1.0 publishes
CPython 3.12 Windows x86-64 wheels (as well as CPython 3.9–3.13 wheels for the
other listed desktop targets), declares Python >=3.9, and its runtime floors do
not conflict with MicroClaw's current numpy/scipy/tifffile floors. That is
evidence for this release, not a promise MicroClaw carries forward. The
trusted-catalog compatibility record and publisher CI must establish it again for
every release. The 0.1.0 files and the GitHub commit are not signed; therefore
they are useful development inputs but do not yet satisfy a policy that requires
publisher signatures.

SMAPpy must make these publisher-owned changes before admission:

1. Publish one immutable skill artifact containing `SKILL.md`, the manifest,
   the exact `smappy-smlm` release and dependency lock (with artifact hashes),
   and the protocol runner. The Python wheel may be referenced rather than
   nested, but every selected file is exact and hashed. A range such as
   `smappy-smlm>=0.1` is not a lock.
2. Expose `microclaw.analysis.v1`, initially with operations `self_check` and
   `localize_ndtiff`. It accepts a JSON job on stdin (or at a job-file path) and
   emits newline-delimited status plus one terminal JSON result on stdout.
   MicroClaw does not import `smappy`.
3. Keep the copied-prose/API tests in SMAPpy CI and add a conformance test that
   installs the release from its lock, runs `self_check`, localizes a growing
   NDTiff fixture, and proves partial output remains readable after cancellation.
4. Publish supported OS/architecture/Python tuples and CI results. Missing a
   compatible wheel is `Available, incompatible on this machine`, not a request
   for MicroClaw to learn how to compile SMAPpy.
5. Put support and issue URLs in the manifest. The Extensions panel labels the
   publisher and sends `Report to publisher` there; MicroClaw bugs cover the
   installer/protocol, not localization correctness.
6. **Establish a publisher signing identity and sign the intake submission.**
   The intake above verifies publisher identity and accepts only a signed,
   digest-pinned record, so this is a hard precondition — and it was missing
   from this list while the paragraph two sections up already required it. The
   note that 0.1.0's files and its GitHub commit are unsigned is not a
   background remark: it is this item, unscheduled. An unlisted precondition is
   one nobody does.

MicroClaw owns the other half: generic install/verify/activate/remove/rollback,
the process supervisor, the typed job/result protocol, acquisition lifecycle
events, artifact presentation, and conformance tests that work without SMAPpy.
This is enough integration to make the package usable without becoming its
bridge maintainer.


### Register rows this leaves behind

For `design/70-carried-forward-register.md` (not edited from here):

| Row | Where it settles | Importance |
|---|---|---|
| Open the community skill package notebook from the settled intake, isolation, export and observer decisions above | local | high, blocks any community skill |
| Build generic package/protocol conformance with a fixture while SMAPpy prepares its release | local | high |
| Pin the three export behaviours before the runner: `@emits`-as-comment for the analysis tool, trigger identity in the acquisition's record, analysis failure kept out of `_recorded_outcome`'s two shapes | local | high, an unpinned one repeats 43h/47/52a |
| The SMAPpy publisher-owned precondition list above (six items, signing included) is not ours to schedule | external | medium, blocks SMAPpy admission but not generic work |
| `run_mda` bypasses `_acquire_with_hooks`, so any acquisition-lifecycle event is invisible to MMStudio MDA runs | local | low, pre-existing |

---

## Blocks

Blocks 71a–71c are the whole notebook: the first-party extra mechanism, its
survival across an update, and a skill that names one. They need two
demo-machine trips and nothing else. There is no 71d or 71e — see *Community
skill packages* above for the separate notebook brief.

### 71a — the extension model, the endpoints, and the panel

**Items**

1. `microclaw/extensions.py`: `EXTENSIONS`; `ExtensionInstallError`;
   `available()` (name, description,
   requirement strings, packages, requiring skills, ready, recorded);
   `requirements_for(name)` reading `Requires-Dist` and refusing non-`extra`
   markers; `ready(name)` by dist-presence **and** import; `record`/`forget`
   against `user_data_dir()/extensions.json`.
2. `updates.locate_uv()` beside `locate_git()`, used by `stage_cached_candidate`
   too. Lift `write_state`'s atomic replace into `_write_json_atomic`.
3. `install(name)`: enumerate installed distribution metadata → canonical
   `name==version` pins → constrain → install → `invalidate_caches()` → import
   → record. The enumeration occurs in this call and is never persisted
   (measurement 2); malformed identities and conflicting duplicate versions
   refuse rather than leave a distribution unpinned (measurement 1); the
   selected interpreter enumerates its effective installed paths under `-I`
   from an empty temporary CWD, and that same interpreter is passed to uv (v1
   selects `sys.executable`), so `purelib`, `platlib` and `.pth`-based editable
   installs are covered without caller-CWD metadata (measurement 4). Every
   failure returns a sentence
   naming the cause; a resolution conflict says which requirement fought which
   pin, and a failure whose cause could be an index override **names the
   variable** (`UV_INDEX_URL`, `UV_DEFAULT_INDEX`, `UV_EXTRA_INDEX_URL`,
   `PIP_INDEX_URL`) rather than quoting a URL — 58e's lesson.
   Requirement-string parsing uses `packaging.requirements.Requirement`, which
   means `packaging` joins `[project].dependencies` — it is already installed on
   every rig via `scikit-image`, but as a transitive dependency nobody
   promised.
4. `GET /api/extensions`, `POST /api/extensions/install`. One job lock, shaped
   like `update_job`. Refuse (409) on the four `/api/update/restart` already
   guards (`webserve.py:898-907`) — a turn holding `session.lock`, an
   acquisition ledger in flight, a pending confirmation, and a **setup write in
   flight**, which this design's original list omitted — plus a fifth of its
   own, update staging running. Rate-limit the install route with
   `_RateLimiter`.
   Exclusion works in both directions: while installation runs, refuse a new
   turn, update staging, restart, or another install with 409. Check and
   reserve admission atomically with the existing session/job synchronization,
   and release the reservation on success and every failure. This is
   coordination within the running server, not ownership of the microscope
   across processes.
   **Note what "atomically" spans**: `session.lock` is an `asyncio.Lock` and
   `update_job_lock` is a `threading.Lock`, so one reservation covering both is
   the hardest thing in this block, not a detail of it. Design it before the
   endpoints.
   **Acquisition and setup-write entry need no guard of their own; the turn
   guard is their guard.** Both are reachable only from a tool call inside a
   turn, and a turn cannot start while an install holds admission — so this is
   a property to state and test, not a second refusal to write. Test it where
   it is observable rather than where it is stated: every pycro-manager
   acquisition funnels through `_acquire_with_hooks` (`tools.py:4659`, holding
   the only runtime `Acquisition` construction at `:4883`), so count entries
   there and assert zero.
   Then put a one-line refusal at that chokepoint anyway, commented as
   unreachable through the turn path and explaining why it exists: it is where
   a *future* non-turn path would trip, and it is the difference between a new
   path failing loudly and racing silently. One labelled unreachable line at a
   chokepoint is worth it where five scattered ones would not be — and the test
   below calls it directly, so it ships executed rather than as 58a's
   never-reached `github:` guard.
   **`run_mda` is not behind that chokepoint.** It drives MMStudio's own MDA
   through `ctrl.studio.acquisitions()` (`:11935`) and constructs no
   `Acquisition` at all, so neither the property nor the refusal covers it —
   the same boundary that already keeps `on_dataset_created` triggers away from
   MDA runs. Say so; do not imply the chokepoint is complete.
5. Panel markup in `serve.html`, which only *calls* the view
   (`serve.html:242`); the pure `Transcript.extensionsView(state)` goes in
   `transcript.js` beside `updateBannerView` (`transcript.js:282`) and is
   exported alongside it, so the rendering is testable without a browser. Four
   states, not six: not installed / installing / ready / failed-or-missing.
   **Installing carries the uv phase**, not just "a thread is alive" — R131 is
   the same banner one feature over, and it was read as hung while working.
   Expose our `checking environment` phase, then `running package installer`
   until uv reports progress. Map its messages to milestones: `Resolved` →
   `Resolution complete`, `Downloading <package>` → `Downloading <package>`,
   `Downloaded <package>` → `Downloaded <package>`, `Prepared` →
   `Package preparation complete`, and `Installed` → `Package installation
   complete`. Completion messages do not claim a phase is still running, and
   installation is not readiness: our import check reports `verifying` next.
   Publish these observations while uv runs, not after it exits; unrecognized
   output must not invent a phase. Three consequences from measurement 6: the stream is
   **stderr**, which is also where item 3 gets its conflict text, so read it
   once and fan out; `subprocess.run(capture_output=True)` buffers until exit,
   so this is `Popen` plus a reader thread, keeping `stdin=subprocess.DEVNULL`;
   and `Downloading` is per package, so the panel names a package and never a
   percentage.
6. `ilastik_adapter.py`'s two messages (`:371`, `:424`) point at the panel.

**Tests** — the ones that must be watched failing on the pre-change tree are
marked ✦; the rest are regression or structure tests (mutate, don't watch).

- ✦ An install request for a name absent from `Provides-Extra` is refused, and
  **no uv subprocess is spawned** — assert on the spawn, not just the status.
- ✦ The argv passed to uv contains only strings drawn from `Requires-Dist`;
  a request body carrying `"ilastik; rm -rf /"` or `"h5py"` never reaches it.
- ✦ `--constraint` is present, and the file contains **only** canonical
  `name==version` lines from installed metadata. Fixtures include editable and
  direct-URL distributions exposed through target-site `.pth` files, plus a
  distribution in a distinct `platlib`; all produce pins. Conflicting duplicate
  versions and a distribution without a usable name/version refuse before uv is
  spawned. The conflict message carries the public diagnostic origins, and the
  fixture is the *same-directory* pair — two `baz-*.dist-info` in one
  site-packages, whose origins are identical (measured), so the assertion is on
  the two versions plus the directory, never on two distinct paths.
- ✦ The selected target interpreter is both the enumerator and uv's `--python`;
  a fake second interpreter with a distribution absent from the server process
  still pins that distribution.
- ✦ The pins are identical whatever the caller process CWD is — run the builder
  from a directory holding a planted `foo.egg-info`, and assert `foo` is absent.
  (Measurement 4: a bare in-process `distributions()` pins it, one entry more
  than the isolated run returns. Assert the *difference*, never either count —
  the environment has already grown by two since that measurement and CI's is
  different again.) Assert on **`-I`**, which is the condition that does the work:
  dropping it reintroduces `foo` and also loses `PYTHONPATH` isolation, while
  dropping the empty CWD alone changes nothing measurable. Do not write a
  fixture claiming both conditions are individually load-bearing — one of them
  cannot fail, and *a limb that cannot fail is not a criterion*.
- ✦ A `.pth` in the target environment that prints to stdout does not break the
  install. Plant one whose import writes a banner, and assert the pins are
  complete and correct. (Measurement 5: with stdout as the payload this refuses
  a healthy environment with `Expecting value: line 1 column 1`.) Write the
  fixture as a real `.pth` in a real throwaway environment — a fake subprocess
  returning clean JSON is the assumption, not a test of it.
- The helper writes its payload to the argv path and the caller never parses
  stdout; a run whose stdout is pure noise and whose file is valid still
  installs, and a run with valid stdout and a missing file refuses.
- The helper output uses `locate_file("")` and no private distribution fields;
  a distribution double without `_path` still produces a useful origin.
- ✦ The constraint file is built inside `install()` from live metadata, not read
  from disk — mutate a recorded copy and assert the install still pins the
  installed version. (Measurement 2: a stale pin is an instruction to downgrade,
  not a veto.)
- ✦ A requirement above its pin surfaces as a conflict with both versions named,
  and **nothing is installed or uninstalled** — assert on the uv argv and on the
  environment being unchanged, not on the message.
- ✦ No module under `microclaw/` imports an `EXTENSIONS` extra's top-level
  module at module scope. (Measurement 3: the no-restart claim is a property of
  the consumer, and `ready()` importing in its own frame cannot see the
  difference.)
- ✦ Readiness is false when the distribution is present but its module raises on
  import (fake a module that raises `ValueError`, which is h5py's real numpy-
  mismatch failure, not `ImportError`).
- An extra whose requirement carries `; python_version < "3.13"` is refused.
- 409 for each of the **five** in-flight conditions, parameterized over them.
  The one this design's original list of four omitted is a setup write, which
  `/api/update/restart` has guarded since before this notebook was written
  (`_microclaw_setup_write_capability.in_flight`, `webserve.py:905-907`).
- Hold an install at a deterministic barrier and attempt a turn, staging,
  restart, and another install: each refuses without starting work. Test both
  admission orderings and a concurrent start; only one conflicting operation is
  admitted. Success and failure both release admission so a later request can
  proceed.
- No acquisition begins while an install holds admission — asserted as the
  **observable effect**, counting entries to `_acquire_with_hooks` and
  asserting zero, driven through `POST /api/prompt` because that is the only
  path that reaches it. It passes *because the turn refused*, and that is the
  point: it pins the property without claiming a guard the turn path cannot
  reach. Not ✦ — on the pre-change tree there is no install endpoint, so there
  is nothing to watch fail; this is structure, so mutate instead.
- The chokepoint refusal is executed, not merely shipped: call
  `_acquire_with_hooks` directly with admission held and assert it refuses.
  Without this the line is an unreachable branch that reads as covered, which
  is exactly 58a.
- Control uv discovery explicitly: PATH present, PATH absent with the Windows
  fallback present, and neither present. CI must not depend on host uv.
- `extensions.json` survives a concurrent `write_state` (the reason it is its
  own file) — assert both files after interleaved writes.
- `Transcript.extensionsView` renders installed / not installed / recorded-but-
  missing / install-failed, from fixtures.
- Drive a fake uv through captured progress messages while keeping it running.
  Between messages, query `GET /api/extensions` and render its state: the phase
  and displayed text must change before process exit. Also cover an
  unclassified interval, verification, and terminal success/failure. A static
  installing banner must fail this test. The messages come from measurement 6's
  transcript, **on stderr**, one line at a time — a fake that writes them to
  stdout, or all at once, tests neither the stream nor the streaming.
- `test_missing_h5py_names_optional_extra`
  (`tests/test_ilastik_adapter.py:382`) asserts today's `microclaw[ilastik]`
  wording, so item 6 changes it rather than adding a test. Existing endpoint
  tests also gain coverage of admission while an extension install runs.

**The fake trap.** The uv fake must be written from uv's actual behaviour, not
from our caller: a resolution *conflict* exits nonzero with the explanation on
stderr, `uv pip` is a uv subcommand, and a plain `uv venv` has no pip module.
Capture a real uncached install transcript for the progress parser and a
nonzero conflict transcript for its retained error text, both on stderr
(measurement 6). Keep measurement 1's real editable-freeze
transcript as the regression evidence for why freeze text is not used, while
the metadata fixture represents that same installed distribution. *A fake that
encodes your assumption is not a test of it.*

**Gate — demo machine, a program** (`design/71-block71a-demo-gate.py`, invoked
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
during a turn returns 409; and the constraint file the install actually wrote is
captured and asserted to be all-`==` on a machine where the slot install is
*not* editable, so the limb is not merely re-testing the dev checkout. Carry a
**control that fires**: a deliberately impossible extra name must fail the limb
if the endpoint accepts it, so the refusal limb cannot pass by doing nothing.

The progress limb captures phase observations while the real install is still
running and verifies that the panel renders their changes. If the install
finishes too quickly to observe a transition, report NOT EXERCISED for this
limb; terminal success alone is not progress evidence.

The gate runs on the **demo machine's managed install**, where `sys.executable`
is `env-<active>/Scripts/python.exe` and microclaw is installed non-editably —
a different freeze shape from the dev checkout, which is the point of running it
there.

### 71b — carry extensions across an update, a rollback, and a reinstall

**Items**

1. `stage_inactive_slot` builds `f"{source}[serve,<recorded extras>]"`, falls
   back to `[serve]` on failure, records `extras_error`, and stages either way.
   The extras attempt must be tried and discarded **before** the existing
   failure branch writes anything: that branch sets `build_error`,
   `build_failed_commit` and `build_error_detail` and raises on the first
   nonzero exit (`updates.py:450-461`), and the head of the same function then
   refuses that SHA until `next_check`. An extras failure must not poison a
   commit that builds fine without the extra.
2. Startup reconcile: recorded-but-not-ready is surfaced in the panel with a
   Reinstall button. Local check only; no network, no automatic install.
3. `update_status()` gains nothing — the extras error belongs to the extensions
   panel, not the update banner. Two banners, one subject each.

**Tests**

- ✦ With `ilastik` recorded, the staged install spec is `[serve,ilastik]`.
- ✦ A failing extras install still publishes `pending-slot.txt` and still passes
  the smoke check; `extras_error` is recorded. (Watch this fail by making the
  fake uv fail only on the extras spec.)
- ✦ After an extras-only failure, `build_failed_commit` is **absent** and a
  second staging call for the same SHA is not refused — the poison-key
  regression, asserted on the state file rather than on the return value.
- The smoke check is unchanged — it must not import an extension.
- Reconcile reports missing-after-rollback from a fixture where the record names
  an extension the running slot cannot import.
- Reconcile keeps a recorded extension ready when it remains importable after
  an ordinary reinstall, and offers Reinstall when a rebuilt environment lacks
  it. Both states are fixtures, independent of the test host's installed extras.

**Gate — demo machine, one program with 71c's limb appended.** A real update
cycle: install `ilastik` from the panel, stage an update, restart, then assert
the extension is *still* ready in the new slot and that `microclaw-slot.json`
names the new commit. Then run `install.bat` against that working environment
and assert the extension remains ready. For the recovery limb, stop the server
and preserve the managed environment outside its slot in the disposable demo
installation; retain `extensions.json`, then run `install.bat` to build a fresh
environment. Prove that `h5py` is absent before launching the server, assert the
panel reports recorded-but-missing, and use Reinstall to restore readiness.
**Delete the preserved environment only after recovery is verified, and report
the cleanup.** If rebuilding or verification fails, stop any server using the
replacement, move the failed replacement aside, and restore the preserved
environment to its original slot before exiting nonzero. Restore any selector
or state files changed by the recovery limb from snapshots taken before it;
verify the restored environment starts. If restoration itself fails, retain
the preserved environment and report its path and the recovery error explicitly.
Block 5b's
gate planted a delayed failure by leaving durable state pointing at a fixture;
an orphaned slot environment is the same family, and a gate that moves
production state owns putting it back. Score it from `extensions.json`,
`update-state.json` and the slot markers, not from the banner text.

### 71c — a skill declares its extension

**Items**

1. `_parse_skill`'s exact-set check `set(metadata) != {"name", "description"}`
   (`skills.py:48`) becomes an allowed-key check with `requires` optional;
   `SkillMetadata` carries it. Malformed shape raises as the other frontmatter
   errors do; unknown extra names do **not** raise at import, because
   `SKILL_CATALOG` is built at module import (`skills.py:101`) and a bad
   contributor edit must not take the application down.
2. `load_skill_text` prefixes the one informational line when a required
   extension is not ready. It returns the **whole file including frontmatter**,
   so the line lands above the opening `---`; the tool `load_skill` passes it
   through unchanged and keeps `@emits_nothing`.
3. `test_skills.py`: every `requires` name is a key of `EXTENSIONS`, and every
   `EXTENSIONS` key is a `Provides-Extra` of the built distribution — the second
   asserted against the built wheel, alongside
   `test_built_wheel_contains_the_source_tree_skill_catalog`.

No skill in the tree declares `requires` yet, so **write a fixture skill that
does** — the shape of the input is the whole point, and a branch no fixture
reaches is a branch that ships unexecuted (58a's `github:` guard).

**Gate**: one limb on 71b's program — `load_skill` on a fixture skill naming an
uninstalled extension returns the file *plus* the line, and the same call after
installing returns the file alone. Score it by diffing the two responses, not by
grepping for the sentence: a limb that only greps passes if the line is added
unconditionally.

## Settled by the operator, 2026-09-18

**`ilastik` is the only first-party extension at v1.** More land the day an
extra and a line in `EXTENSIONS` arrive together; 71a's gate exercises exactly
what ships.

**A recorded-but-missing extension is not reinstalled automatically.** The panel
reports it and the Reinstall button is the user's, as written above. The
operator asked for a reason not to do it automatically, since being made to
reinstall extensions after reinstalling MicroClaw would be annoying. Three
answers, and the first is the one that decided it:

- **The case is narrower than "after a reinstall".** `install.bat` reuses a
  working environment and clears it only when the interpreter fails to *run*
  (`install.bat:178-183`), so an ordinary reinstall preserves `h5py`; 71b item 1
  makes staging build `[serve,ilastik]`, so an ordinary update carries it too.
  What is left is a genuine environment rebuild after a dead interpreter, a
  rollback to a slot predating the install, and a staged extras failure.
- **It is not the two-line change this document called it.** An install holds
  71a's admission and refuses turns, staging and restart; started at launch it
  would 409 the user's first prompt for something they never asked for, which is
  R131 one feature over and a worse annoyance than a button. It also needs an
  attempt record keyed to the environment or a failure repaints the banner and
  hammers an index on every launch — *a record that some earlier attempt failed
  is not a record of this one*.
- **The panel and the button have to exist regardless**, because measurement 2
  makes a requirement above a pin a clean `No solution found`: an automatic
  attempt can fail legitimately and still leaves the user pressing something.

There is no argument of principle against it — `extensions.json` **is** the
user's consent, and startup is the safest moment for an in-place install, with
nothing imported and no acquisition in flight. If the three narrow states above
are measured annoying in practice, the shape to build is `start_due_check`'s
(`webserve.py:1385`): background, off the startup and request paths, yielding
admission to a user turn rather than blocking it, one attempt per environment.
That measurement buys it; nothing else does.

## 71a gate results — demo machine, 2026-09-21

Three rounds. **Round 3 passed eleven of thirteen limbs**; the two NOT EXERCISED
were `initial panel render` and `progress render`, both blocked by node being
absent on that machine. Node is a gate dependency, not a product one, and
`Transcript.extensionsView` is covered by fixtures in `tests/test_transcript_js.py`,
so they were accepted rather than chased.

Measured, scored from the artifacts rather than the verdict:

- **No restart is needed.** `h5py` imported inside pid 6120 — the server process
  already running when the install was requested — and the real ilastik adapter
  read a real `.ilp` through it. This is the claim the whole block exists for and
  it is the one the gate proves.
- **One uv invocation**, confirming the dry-run's removal, at
  `uv pip install --python <slot> --constraint <tmp> h5py>=3.10`.
- **55 pins, every one `==`**, on a non-editable managed slot: `microclaw`
  excluded, `h5py` absent (not yet installed), and `numpy==2.5.3` held. The
  install could have moved numpy under a running server and could not.
- **The panel changes phase while uv runs.** With uv's cache cleared:
  `checking environment` → `running package installer` → `Downloading h5py` →
  `Package installation complete` → `verifying`, five changes over a 1.02 s
  install, two of them uv's own milestones. R131's "is it hung?" is answered by
  observation here, not by argument.
- `installed_commit` and the `extensions.json` record both carried the branch
  HEAD, so the run is tied to a tree rather than taken on trust.

**Three of the three gate rounds failed on the instrument, never on the
product.** Round 1 ran against the slot's installed `microclaw` because the
runbook did not say to run `install.bat` — the step was already recorded in
`design/58-block58d-demo-gate.md` and was invented instead of looked up. Round 2
lost two limbs because each asserted a product claim and a node-only rendering
claim together, and lost `under-test.json` to PowerShell stripping the inner
quotes out of a `python -c` string. Round 3 needed `-Fresh`, because the first
successful run leaves `h5py` installed, the record written and uv's cache warm —
a repeat run then tells you strictly less than the one before it.

**What has no rig evidence.** `locate_uv()`'s `%USERPROFILE%\.local\bin\uv.exe`
fallback: the artifact shows `...\.local\bin\uv.EXE`, which is `shutil.which`
returning the PATHEXT casing from **PATH**, not the fallback firing. The same
casing assumption failed a Windows CI test, which is how it was caught.

## Run ledger

| Block | Branch | Start commit | Status |
|-------|--------|--------------|--------|
| 71a | `block-71a` (deleted) | `720309b` | **merged 2026-09-21** as `2ad4e13`, PR #32 |
| 71b | — | — | not started |
| 71c | — | — | not started |
