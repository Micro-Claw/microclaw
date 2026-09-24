# Community skill packages — accepting one without becoming its maintainer

Opens `R82`. `design/71-installable-extensions.md` §"Community skill packages —
next notebook brief" and §"Feasibility against SMAPpy 0.1.0" are this
notebook's starting material and are **not restated here**; read them first.
This document records what that brief left open: the sequencing, the transport,
and the blocks.

Nothing in 71a–71c is a step toward this. `microclaw/extensions.py` installs a
**first-party extra** named by our own metadata, into our own environment. A
community package is a different product — a package format, an intake with a
trust policy, a per-release isolated environment, an out-of-process supervisor,
a versioned job protocol, an acquisition trigger, and a new agent tool. It must
not be reached by extending `extensions.py`.

## Sequencing — the first decision, taken before any code

`R82`, `R83` and `R84` have no owning coordinator, and two of them are written
as "before the notebook". Neither is.

**`R83` is this notebook's first block, not a precursor.** A fixture package is
not a thing you can build first: it is a *conformance fixture for a protocol*,
and the protocol is the notebook's core content. Written before the manifest
schema and the job protocol exist, a fixture would either invent them unowned or
be re-written when they land. Block 83a specifies the package format and
supplies both fixture manifests and assets; 83c specifies the wire protocol,
adds the executable fixture's runner, and executes it against that protocol.
`R83` closes only after both, so the first real package never becomes the
specification. The register's *importance and ease* rule is satisfied by
ordering **inside** the notebook, not by preceding it.

**`R84` is two things and neither is a standalone block.** The register scores
it HIGH/SMALL and "pin these before a runner is handed the notebook". Checked
against the code, that is optimistic in one of its three parts:

| `R84` behaviour | Testable today? | Where it lands |
|---|---|---|
| `_recorded_outcome`'s two structural shapes (`tools.py:1040`) | **Yes** | 83a, as a stated constraint on the result schema plus one characterization test |
| `refuse()` plants `raise RuntimeError` (`tools.py:2284`, reached from `tools.py:2316`) | **Yes**, and already covered many times over in `tests/test_session_script_export.py` | nothing new — it is an *input* to the marker decision, not a thing to pin |
| `@emits`-as-comment for the analysis tool | **No** — there is no analysis tool to hang a renderer on, and a renderer cannot be tested without one | 83e, with the marker written into the block statement as non-negotiable |

The protection `R84` actually wants for the third item is not a test that cannot
exist. It is that a runner must not be left to *choose* the marker: 83e's block
statement fixes it as `@emits` with a comment-only renderer, and says why
(`@refuses` strands every later step; `@emits_nothing` is silence about an HDF5
the session really produced). A comment-only renderer works mechanically today —
the export loop extends the body with `rendered.splitlines()` and counts the
call as emitted (`tools.py:2355`), so a renderer that returns only `#` lines
emits and continues.

The first item is sharper than the register states it, and the sharpening is the
useful part. `_recorded_outcome` scans **every top-level list-valued key**, not
just `results`. So the constraint on an analysis failure record is exact:

- never a top-level `error` key (the exact key; `error_um` is a measurement);
- never an entry carrying `error` inside **any** top-level list;
- therefore recorded under a nested **dict**, e.g.
  `{"analysis": {"state": "failed", "error": "..."}}`.

A top-level `"analysis": [...]` of per-frame dicts carrying `error` would trip
the list scan and turn a flawless acquisition into a `refuse()` at export time.
That is the defect the row exists to prevent, and it is one `isinstance` away
from being written by accident.

## Trust boundary — publisher code runs with the user's permissions

V1 admits trusted publisher code. A per-release environment isolates dependencies;
a subprocess separates execution from MicroClaw's Python runtime. Neither is an
OS sandbox. A worker running as the same user can access that user's files and
network services, including hardware services reachable independently of
MicroClaw. Signatures establish publisher provenance, not safe behaviour.

The supported contract is a read-only dataset observer that writes only its
reserved output directory. MicroClaw passes data and validated parameters, never
a controller, guard, hook object, token or hardware server URL, and exposes no
worker operation that grants hardware authority. These are API and publisher
obligations, not enforced filesystem or network confinement. The install/enable
UI must disclose execution with user permissions. Running hostile code safely
would require a separate OS confinement design; v1 does not claim that guarantee.

Skill text also grants no authority: loading instructions cannot enable a
trigger, approve execution, or bypass existing tool authorization. Installing,
enabling discovery, and consenting to execution are distinct recorded decisions.

**Markdown-only packages also require scrutiny.** Catalog metadata is presented
to the agent; today `load_skill` returns the full skill body as a tool result,
not as system-prompt text. External skill bodies must keep that distinction and
be labelled with publisher and release provenance. Their instructions can still
influence calls to tools already authorized at real hardware. The tool layer's
refusals, envelopes, write budgets and confirmations remain the enforcement
boundary, regardless of who wrote the prose. Both package kinds therefore take
the same admission policy, and enabling discovery is a recorded user decision.

## Transport — follow the dataset, supervise over pipes

V1 follows the growing, collision-resolved NDTiff dataset on disk. This uses the
existing acquisition output boundary without adding a frame transport or running
third-party code inside an image callback. OS caching may help, but neither
zero-copy access nor a performance advantage is a requirement or an established
measurement here.

Control uses stdin/stdout with newline-delimited JSON: an initial job followed
by lifecycle/cancellation messages on stdin, status messages and one terminal
result on stdout. Stderr is diagnostic output. The supervisor owns the pipes
and process lifetime. HTTP is unnecessary for this local contract; it does not
inherently grant hardware authority or cause acquisition backpressure. Shared
memory, Arrow IPC or another transport needs evidence of a disk-path bottleneck
and a separate design that preserves the observer boundary.

**Acquisition never waits for worker startup, progress or completion.** Trigger
dispatch is bounded and non-blocking; a full dispatch queue records an analysis
failure instead of delaying frame capture. Process launch, pipe writes, pipe
reads and worker shutdown happen outside acquisition callbacks and the capture
thread. The supervisor drains stdout and stderr continuously, bounds message
sizes and retained logs, and handles unresponsive or malformed workers with
finite deadlines and process cleanup. Analysis failure never becomes an
acquisition error. CPU, memory and storage contention remain possible: v1 does
not promise that a concurrent worker can never affect acquisition performance.
Concurrency limits and a slow/noisy-worker test are part of 83c.

**An acquisition return is not proof that its writer stopped.** Lifecycle
messages distinguish the acquisition outcome from writer state. In particular,
design/60's unterminated return reports writer state as unknown or still running;
it must not assert a final frame count or a finalized dataset. A later observed
teardown completion may update that state. The worker can finish with a labelled
partial artifact after cancellation or a deadline without declaring the input
complete. If the worker has already exited, the supervisor records undelivered
notifications; delivery is never a condition for acquisition to return.

## Blocks

The contracts below are fixed now; implementation details for later blocks are
designed when reached. **Letters follow execution order**, 83a through 83f. Two
dependencies set that order: the supervisor must exist before installation can
activate a candidate on the strength of `self_check`, and catalog promotion
reuses the same conformance runner. 83f completes the publisher-to-user path;
until then, fixture installation is an integration test, not a shipped community
catalog.

**Six blocks, not four, because two of the original 83a's bullets were design
areas wearing a bullet's clothes.** A publisher trust model — signed payload,
identity binding, rotation, revocation, verification roots — is not a schema
field, and 83f's revocation semantics are defined by it, so it gets its own
block. And specifying a wire protocol in one block while executing it two blocks
later ships a spec no code has run: its "protocol transcripts" would be fixtures
written from our own assumption about what a worker emits, which is the defect
this repository has paid for repeatedly. The protocol is specified and
implemented in the same block, against the fixture that has to satisfy it.

### 83a — package format, manifests, and fixtures

The unit is an immutable publisher release carrying `SKILL.md`, assets and a
manifest: package ID, publisher, version, compatible MicroClaw range, package
kind, license, source and issue URLs, and immutable artifact identity. Executable
packages additionally declare Python/platform compatibility, protocol version,
entry point, typed operations and per-platform dependency locks with artifact
hashes. **Markdown-only packages need no Python environment, dependency lock,
entry point or worker.** Both kinds use the same signed admission policy.

This block builds the format and validators, a Markdown-only fixture, and an
executable-package fixture's manifest and assets in `tests/fixtures/`. The latter
is a format fixture, not yet a runnable release: its declared runner is supplied
in 83c, together with the protocol it implements. No installer, subprocess
execution or network is required here. Protocol-version and operation fields
have structural validation in 83a; supported versions and operation semantics
are enforced in 83c before any executable release can be admitted.

What it must settle:

- Manifest schemas and field-specific refusals. A dependency range is not a
  lock. Entry points are structured declarations, never arbitrary shell text.
  The external intake record pins the release artifact digest; the artifact
  does not contain a digest of itself. Extraction paths must stay within the
  release directory; escaping paths and links refuse.
- The analysis-result constraint from `R84`: analysis status/errors sit beneath
  a nested dictionary. Characterize `_recorded_outcome` to prove that this shape
  is invisible to it and top-level lists of per-item errors are not.
- External skill identity and loading. Keep packaged `SKILL_CATALOG` unchanged;
  external names are qualified by publisher/package/skill and resolve only
  through explicitly enabled, verified installed records. Bind the loaded text
  and operation declarations to the same release digest. No directory scanning,
  implicit entry-point discovery or shadowing of built-in skill names.
- After the user enables discovery, the catalog line is agent-visible without
  a separate consent for each presentation; merely appearing in the remote
  catalog does not expose a package to the agent. `_parse_skill` already checks
  first-party descriptions for nonempty text and rejects `\n` and unknown
  frontmatter keys (`skills.py:33`). External metadata needs explicit length
  bounds and rejection of line separators and control characters in every
  rendered field, including publisher/package/skill identifiers, not just the
  description. Define identifier syntax so qualifiers remain unambiguous. Render
  the line as publisher-provided metadata with provenance. These checks bound
  size and preserve formatting; even a short single-line description can contain
  instructions, so they do not establish trust or grant execution authority.

Acceptance: both fixture kinds validate; mutations exercise every refusal with a
named field, including oversized descriptions and line/control characters in
each rendered metadata field; the `_recorded_outcome` characterization passes.
This establishes format conformance; trust is accepted in 83b and executable
protocol conformance in 83c. Local only — no gate.

**What 83a settled** (merged 2026-09-22, PR #36; `microclaw/skill_packages.py`,
`tests/test_skill_packages.py`, `tests/fixtures/skill_packages/`). Identifiers
are `[a-z][a-z0-9]*(?:-[a-z0-9]+)*` and the qualified external name is exactly
`publisher/package/skill`; no component can contain `/`, so no external name can
equal a packaged catalog name, which is asserted against the live
`SKILL_CATALOG` rather than a copy of it. Refusals carry the failing field as a
dotted path on one exception type (`PackageRefusal.field`) — that is what makes
"names the field" an assertion rather than a substring match. Each kind's
manifest is a **closed** mapping, so a Markdown-only manifest declaring
`entry_point`, `locks`, an environment or a worker refuses as an unknown field,
and so does a manifest carrying a digest of its own release; only the external
intake record pins that digest, and it carries no signature field at all,
because that is 83b's. Every rendered field has a named length bound and every
collection a count bound: `_text` takes its limit positionally, so a field
cannot be added unbounded by omission. Loading verifies **every** declared
asset's digest, not just `SKILL.md`, so a changed ancillary file cannot
accompany intact prose.

One shape is deliberate and will change in 83e: `external_catalog_lines` raises
on the first malformed enabled record rather than isolating it, and says so in
its docstring, because the disabled-and-why state belongs to discovery.

### 83b — publisher trust: signature, identity, rotation and revocation

The signature format, the exact signed payload, and the publisher identity
binding. The payload binds package ID, publisher, version, artifact reference
and digest, and compatibility metadata. Publisher keys or identities must be
admitted by MicroClaw policy, not trusted because a submission supplies its own
key. Define key rotation and revocation, and the catalog verification roots.
Revocation's effect on an installed release's *future execution* is defined
here, because 83f only surfaces it.

Acceptance: missing, invalid and forged signatures refuse; unknown publisher
identities refuse; a changed payload and a digest mismatch refuse; each names
the field that failed. The unsigned `SMAPpy` 0.1.0 case (`R85`) fails admission,
which is the cheapest possible proof the policy is real. Test signatures use an
explicitly test-only trust root that cannot be mistaken for a production one.
Rotation and revocation tests cover installed releases and stale/offline trust
state as well as new submissions. Specify when reauthorization is needed, how
future starts are refused, and what happens to already-running jobs; no trust
refresh may block acquisition or delete its data. Local only — no gate.

**What 83b settled** (PR #37; `microclaw/skill_packages.py`, trust fixtures
under `tests/fixtures/skill_packages/trust/`). One algorithm, Ed25519, via
`cryptography` as a **hard** runtime dependency: every path that loads or starts
an external release verifies it, so a missing library must never be an ordinary
installed state. A signature is `{alg, key_id, value}`; `key_id` is the SHA-256
of the raw public key and is recomputed wherever a key is admitted, never
trusted. Signed bytes are the canonical JSON (sorted keys, `,`/`:` separators,
ASCII) of the document minus its `signature`, and each document carries a
`type` tag inside those bytes, so a release signature cannot be replayed as a
policy signature.

- **The signed payload is the intake record.** It is closed and now requires
  `type`, `kind`, `microclaw` and, for executables, `python`, `platforms`,
  `protocol_version`, plus `signature`; every compatibility field must also
  equal the manifest's. A submission carrying its own key refuses as an unknown
  field, and a `key_id` the policy has not admitted **for that publisher**
  refuses regardless. A key id may not appear under two publishers.
- **Roots and policy.** The trust policy is MicroClaw's document, signed by a
  root, with `environment`, a monotonic `revision`, `expires_at`, publishers
  with key states, and digest-keyed `revoked_releases`. Its environment must
  equal the root set's. **The production root set is empty**: no MicroClaw
  production key exists, so production verification fails closed until 83f
  creates one — key custody is 83f's decision, not a runner's. The test root is
  `environment: "test"`, `TEST-ONLY` in every filename, and its committed seeds
  say they are public.
- **One gate for the future.** `check_release(intake, policy, *, purpose, now)`
  with `purpose` `admission` or `execution`; the verdict carries `stale` and
  `environment`. `load_external_skill` and `external_catalog_lines` run the
  execution check on every enabled record.

| Policy state | admission | execution of an installed release |
|---|---|---|
| key retired (rotation) | refuses | runs — rotation never needs reauthorization |
| key, publisher or release revoked | refuses | refuses the next start; installed files untouched |
| policy expired (stale/offline) | refuses | runs, `stale: true`; cached revocations still apply |
| no verified policy | refuses | refuses |

Revocation affects **future** starts only: a running job is not cancelled, and
no trust check writes, moves or deletes anything — asserted against an installed
tree and with `socket`/`Path.open` patched to raise. Reauthorization means a new
admission of a different release, needed only after a revocation. A stale
policy's residual is stated, not solved: revocation is only as fresh as the
last verified policy, and freshness is 83f's.

The policy passed to `check_release` is the return value of
`verify_trust_policy`, a caller attestation like a record's `verified` flag.
Round 1 re-verified it on every call against roots that travelled in the same
snapshot — no protection, since whoever can alter the policy can alter those
roots, at a full policy walk plus a signature check per record per catalog
render. It now verifies exactly one signature per release, which a test counts;
a future cache must re-verify a stored document against the module's roots on
load.

### 83c — the job protocol and the supervisor, specified and executed together

The versioned job protocol, including `self_check` and declared analysis
operations. Jobs carry a job ID, pinned package/release/operation, validated
parameters and, for analysis operations, input dataset path and reserved output
directory. `self_check` requires no acquisition dataset. Define input and
result schemas, status/result framing, message limits, unsupported-version
refusals, cancellation, acquisition outcomes and independent writer state.
Exactly one terminal worker result is permitted; exit without one is a
supervisor failure with any valid partial artifacts retained. Specify artifact
descriptors and partial/final validity, not publisher-specific analysis APIs.

The supervisor owns process startup, continuous pipe draining, bounded
queues/logs, deadlines, cancellation and process cleanup on supported platforms.
It provides the same `self_check` and conformance machinery used by installation
and intake. It never imports package code into MicroClaw.

Tests exercise success, malformed/oversized output, output flooding, a worker
that does not read stdin, crash, hang, cancellation and valid partial artifacts.
Prove that a slow worker or saturated dispatch queue does not make acquisition
wait for analysis. Bound concurrent workers. Lifecycle fixtures cover an
unterminated acquisition followed by late writer completion, plus a worker that
exits before that notification. Hardware lifecycle integration lands in 83e.
`R83` closes when these executable conformance tests pass as well as 83a's.

**What 83c settled** (PR #38; `microclaw/skill_packages.py` for the wire
schemas, `microclaw/skill_supervisor.py` for the process — the notebook's one
new module, because `skill_packages.py` promises no I/O and a supervisor is
threads, pipes and deadlines; tests in `tests/test_skill_supervisor.py`).

- **Identity.** Every message carries `protocol: "microclaw.analysis.v1"`; the
  manifest and intake keep `protocol_version: "1.0"`, mapped by
  `SUPPORTED_PROTOCOLS`, exact match only. `validate_manifest` stays
  structural; **support is refused in `check_release`, after the signature**, at
  both purposes, so 83d's startup recheck calls the same gate.
  `supported_executable` additionally requires a declared `self_check` and v1's
  only schema type, `object`.
- **Messages.** Closed key sets both ways. stdin: `job` (release identity,
  operation, parameters; analysis operations add `input.dataset` and
  `output_dir`, `self_check` has neither), `acquisition {outcome, writer}`,
  `writer {state: finished}`, `cancel`. `unterminated` requires
  `writer: unknown`; no frame count can be expressed. stdout: `status`,
  `artifact`, and one `result` whose `failure` is single-line (tracebacks go to
  stderr) and whose `input_complete` is the worker's own claim. An artifact is
  `{path, sha256, validity}` under `output_dir`; the supervisor verifies each at
  the end, never deletes one, and labels every retained artifact `partial`
  unless the worker's `succeeded` terminal stood.
- **Violations are supervisor failures**: malformed, oversized, partial line at
  EOF, wrong protocol/job/type, a second terminal, anything after it, exit
  without one, a non-zero exit after one. Publisher `output` is kept verbatim —
  an `{"error": ...}` there is publisher data, and the record's own fields never
  use `error`, so `_recorded_outcome({"analysis": record})` stays blind to it.
- **Bounds** (constants): message 65,536 bytes both ways, status 512 and failure
  1,024 characters, 256 artifacts, 64 KiB stderr tail, 256 retained status, 8
  pending notifications, 32 recorded notifications, queue 4, workers 2;
  deadlines startup 60 s, `self_check` 120 s, shutdown grace 10 s, and **no
  default analysis deadline**.
- **The grace starts only from a delivered cancel, the terminal, or the
  supervisor closing stdin.** Round 1 also started it when the *worker* closed
  stdin, which made an undeliverable notification kill a working observer —
  delivery gating the run. Worker-side closure now only marks notifications
  undelivered.
- **Tree cleanup after every exit, success included.** Windows: a Job Object
  (`KILL_ON_JOB_CLOSE`), the child created suspended, assigned, then resumed with
  `NtResumeProcess`, so no descendant can start outside the job. POSIX: a new
  session and `killpg`; a descendant that calls `setsid` escapes, accepted off
  the shipping platform. Proved by a grandchild heartbeat that stops, on
  `windows-latest` as well as locally.
- **Timing contract.** `submit` and `notify_*` never raise, never wait and do no
  I/O on the calling thread; launch, hashing, pipe writes and kills run on
  dispatcher and pipe threads, asserted by thread identity. Measured on one Mac
  (macOS 14.5 ARM64, n=300 per row): `submit` p50 0.50 ms / max 0.87 ms with a
  slow worker and 0.49 / 0.62 ms into a saturated queue; notifications p50
  ≤ 0.015 ms, max ≤ 0.21 ms. Attributed: `check_release` 0.29 ms (one Ed25519
  verify), `validate_manifest` 0.10 ms, `validate_intake` 0.05 ms — the latter
  runs twice per submit, once inside `check_release`, which is not worth a
  change. No Windows timing was taken.

Two things for 83d, which owns both. `Supervisor.self_check` waits without a
bound for its turn in the queue — its deadline starts at spawn — so an install
queued behind a deadline-less analysis job waits for that job. And the Job
Object has run only on a CI runner: its first desktop evidence, a worker under a
`serve` launched from the shortcut, belongs in 83d's demo gate.

### 83d — isolated per-release installation and activation

Executable packages get one environment per release under the user data
directory, keyed by ID and digest, installed from the publisher's compatible
wheel lock with hashes required; no source-build fallback. Markdown-only releases
are verified assets and metadata, with no environment creation.

Installation is a durable transaction: verify the artifact and compatibility,
stage the candidate, run 83c's bounded `self_check` for executable packages, then
atomically switch the active pointer. Until that passes, the previous verified
release stays live. Retain one release for rollback, and retain any release
referenced by an active job or enabled pinned trigger receipt. Removing a release
with a receipt requires explicitly disabling or retargeting that receipt; never
silently substitute the active release. Repair, remove and failed-verification
states are explicit; a broken installed skill stays visible and disabled. An
update cannot silently retarget an execution receipt pinned to another digest.

**An installed release lives outside both update slots, and that is what makes
it survive — but its compatibility verdict does not.** `user_data_dir()` is
outside the slots, so package files need not be reinstalled for an ordinary
MicroClaw update. The worker's base interpreter must also live independently of
either replaceable slot: a venv path outside the slots alone does not establish
that. Record its interpreter identity and verify it still starts after slot
replacement; a missing or broken interpreter leaves the release visible and
repairable, never silently ready. What an update also changes is the other side
of a comparison: the manifest declares a compatible MicroClaw range, and
updating or rolling back moves the running version across it. A verdict recorded
when the release was installed is then a cache computed against the previous
install. Invalidate it **at startup of the newly running build, before external
skill discovery or job admission**, not when an inactive candidate is merely
staged.
Recheck the manifest range and supported protocol against the running build;
key cached verdicts to that build and the worker interpreter identity. Check
eligibility again at dispatch, including pinned receipts. Surface the result
in the panel — an installed release that has become incompatible must read as
disabled-and-why, never as quietly absent and never as still available. A
rollback moves the version backwards and must be treated the same way.

Acceptance includes interrupted staging/activation recovery, failed self-check,
rollback, repair/remove, and Markdown-only installation without launching Python.
Available means a compatible non-yanked catalog release exists, not that this
machine can successfully download or install it.

**This block takes a demo-machine gate, and it is the notebook's first.** It is
the block that writes real directories under a real `user_data_dir()`, installs
a real wheel set with a real `uv`, and interacts with the real updater — every
one of which is where design/71 and design/58 found their defects, and none of
which a fake settles. The gate installs both fixture kinds, updates, rolls back,
and reinstalls, verifies worker interpreter survival after slot replacement,
and uses a compatibility control that actually becomes incompatible across a
transition. It is scored from the state files and the panel rather than from a
verdict. Everything a fake can settle is settled in 83a–83c first.

**What 83d settled** (PR #39; `microclaw/skill_store.py`; serve's startup hook,
`GET /api/skill-packages` and the rollback/repair routes in `webserve.py`; a
read-only panel; tests in `tests/test_skill_store.py`; the gate is
`design/83-block83d-demo-gate.{py,ps1,md}`).

- **Layout.** Each release attempt lives at
  `user_data_dir()/skill-packages/packages/<id>/installs/<digest[:16]>-<nonce>/`,
  which holds:
  - `artifact.zip` and the extracted `release/`;
  - `env/` (executables only);
  - `install.json`, whose state goes from `staged` to `ready` or `failed`;
  - `verdict.json`.

  **Activation is one `os.replace` of `pointer.json`** (`{active, previous}`).
  A repair builds a new attempt directory, so a live environment is never
  rebuilt or renamed.
- **Lock.** One lock per package: `os.mkdir`, with the owner's pid and a nonce
  inside. It is stale when the pid is dead; a live lock refuses at once and
  never waits.
- **Recovery.** Each kind of leftover has one fixed rule. **A damaged pointer is
  never repaired by falling back to `previous`**: that would silently swap the
  live release. The package reads `broken` instead and stays repairable.
- **Interpreter.** A CPython 3.12 that MicroClaw pins under `skill-packages/python/`.
  It is installed with
  `uv python install --no-config --no-bin --no-registry --install-dir … 3.12`
  and found with
  `uv python find --managed-python --no-python-downloads`, with
  `UV_PYTHON_INSTALL_DIR` set on that child process only.
  **`--no-bin` and `--no-registry` are load-bearing.** Without them uv writes a
  `python3.12` link into `~/.local/bin` and, on Windows, a PEP 514 registry
  entry, both outside the store. The coordinator's own unsandboxed probe
  planted the first of these on the development Mac.

  The recorded identity is `{executable, base_prefix, version, cache_tag,
  platform, distributions}`, and it is only ever read by **running** the
  release environment's python.
- **Environment.** Built with `uv venv` then
  `uv pip install --require-hashes --no-deps --no-build`, the same two steps the
  update slots use, and one builder serves both install and repair. The proof
  that the lock was installed is that the probed distributions **equal** the
  lock's pins, not that the worker happens to import them.

  `self_check` runs on a **dedicated one-worker supervisor**, so an install never
  queues behind an analysis job that has no deadline (83c hand-off a).
- **Verdicts.** A verdict is keyed to `current_build()` (`__version__`, the slot
  marker's commit and the supported protocols) and to the recorded interpreter.
  One computed against a different build or interpreter reads `unchecked`, in
  every process, with no ordering dependency.

  A restart of the same build keeps its saved verdict until serve's startup
  thread replaces it. That thread runs `recover` and then `recheck`; serve does
  not wait for it, and it runs no publisher code.

  Residual: an interpreter broken between two launches of the same build reads
  eligible until that startup probe runs.
- **Retention.** Every operation that can delete takes a required
  `retained_digests`, which 83e fills in. `resolve(package, digest)` never
  consults the active pointer and never returns a different digest. **Repair
  never changes which release is active.**
- **Trust files** (operator decision, 2026-09-23). `trust/policy.json` is
  re-verified every time it is loaded. `trust/roots.json` is honoured only when
  it says `environment: "test"`, and it turns on a panel banner. The product
  never writes it.

**Review.** Two runner turns plus one coordinator fix.

The start turn stopped on a contradiction in the prompt, and it was right to.
D5 said every release reads `unchecked` until the recheck finishes, which does
not follow from D5's own rule on a same-build restart.

Round 1 returned three defects:
- repairing `previous` activated it;
- removing `previous` deleted it before clearing the pointer, so a partial
  Windows delete broke the whole package;
- two processes could both break one stale lock.

It also returned four surviving mutants out of fifteen. One of them was the
block's central case: **no test had an interpreter that still starts but is
not the one recorded.** The only such test deleted `python`, so the probe raised
anyway. All fifteen mutants are now killed.

**Gate.** The demo machine, 2026-09-24, one run, installed commit `c70cb92`.

13 of the 14 limbs passed as scored. The compatibility limb failed on **the
gate's own prompt**, which named a row label the panel does not print. Its
artifacts meet the limb:
- after the 2.0.0 slot started, every active release is disabled with exactly
  `microclaw: incompatible with 2.0.0`;
- every verdict is keyed to `version: 2.0.0`;
- the operator transcribed that panel row.

The reinstall limb had **no control proving `install.bat` ran**. The operator
confirmed it ran to completion, and the limb now requires the two files
`install.bat` always rewrites to have changed.

Measured on the demo machine, n=1:
- installs took 3.64 s for the first executable release (provisioning
  included), 1.01 s for the second, and 0.05 s for Markdown;
- a startup verdict took 0.10–0.13 s per executable release (one probe) and
  0.007 s for Markdown;
- under a desktop-launched serve, rollback took 0.846 s and repair 1.25 s. Each
  ran a **real `self_check` worker under a desktop `serve`**, which meets 83c
  hand-off (b).

Scoring the artifacts of that 13/14 run found two product defects, both fixed
in `68c8ebe`:
- `job.json` survived a serve restart, so a serve closed mid-repair would have
  left the panel showing "running" and polling every 500 ms forever;
- a failed release's reason rendered a raw Python dict.

**Handed on to 83e and 83f:**
- the production index route (`find_links=None`) has never run, because every
  test and the gate install from the committed wheel;
- repair goes through admission, so a stale policy refuses it; freshness is
  83f's;
- a package whose lock is held when serve starts is skipped by that recheck and
  reads `unchecked` until the next start; 83e's turn-boundary refresh is the
  natural place to recheck it;
- the demo machine's inactive slot still holds the gate's 2.0.0 build, which the
  next real update rebuilds.

### 83e — agent discovery, execution consent, triggers and export

Expose enabled external skill metadata through a discovery mechanism refreshed
at turn boundaries; `agent.py`'s `SYSTEM_PROMPT` embeds `catalog_text()` at
module import (`agent.py:81`, `:420`), so a new file loader alone is
insufficient. Load text by qualified name from the verified release, and ensure
enable/disable/install changes become visible without a server restart. A
malformed external skill disables only its own record. Markdown-only skills use
existing authorized tools.

One generic analysis tool dispatches a declared operation by package, pinned
release and operation name, validating parameters against its schema. No tool
per publisher and no arbitrary Python/shell execution surface. An explicit tool
request requires execution authorization; skill loading or installation does
not supply it. A persisted trigger receipt supplies consent for its declared
workflow or rig profile and pins release, operation, parameters and capabilities.
It can be disabled without uninstalling. Snapshot the receipt when dispatching
so concurrent configuration changes cannot retarget an in-flight job.

Integrate dispatch at dataset creation using the real collision-resolved path.
Record every acquisition outcome and independently observed writer state through
83c's non-blocking supervisor path. The acquisition tool's recorded result
carries the fired trigger's package, digest, operation, parameters and reserved
output directory under a nested analysis dictionary, including dispatch failure.
Asynchronous completion belongs in a durable job record linked from that result;
export must not depend on querying a currently active worker or current config.

The analysis tool takes **`@emits`** with a comment-only renderer: package ID,
release digest, operation, reserved output directory, and a statement that the
analysis was not reproduced with instructions to rerun it. Acquisition emitters
also disclose recorded triggers. Promote the exporter's existing nested
`one_line` (`tools.py:2270`) to a shared helper so the exporter and tool renderers
use the same comment folding. It is currently local to `export_session_script`
and cannot be called directly by another renderer. Fold every value interpolated
into a comment; preserve the original structured parameters in the durable
record for rerunning analysis. A publisher error containing newlines must not
escape its comment and invalidate the session's export. Test that analysis
failure preserves acquisition export and later steps, and that comments safely
handle multiline values. `R84`'s third item closes here. The panel discloses
user-permission execution and that `run_mda` drives MMStudio's engine and fires
no lifecycle event (`R86`).

Acceptance covers discovery after enable/disable, name collisions, Markdown-only
loading, remote catalog entries remaining absent until discovery is enabled,
schema/consent refusals, pinned receipts across updates, lifecycle failure paths
and exported scripts that continue past the disclosure.

**Split into three blocks** (operator decision, 2026-09-24). By this
notebook's own rule, six design areas make more than one block:

- **83e-1:** the discovery refresh, the enable-discovery record, per-record
  isolation, loading by qualified name, and the recheck of a package that was
  locked at startup (83d's hand-off).
- **83e-2:** the analysis tool, execution consent, parameter validation, the
  `@emits` comment renderer, and the shared `one_line`. It fills the live-job
  half of `retained_digests`.
- **83e-3:** trigger receipts, dispatch at dataset creation, lifecycle and
  writer state, the receipt half of `retained_digests`, the acquisition
  emitters' disclosure, and the `R86` panel copy.

The paragraphs above remain the combined statement. Each block takes the
acceptance items that belong to its own areas.

**83e-1 decisions.** D1–D3 are operator decisions from 2026-09-24; D4–D7 are
the coordinator's.

- **D1 — discovery is its own system block.** `_system_blocks` already runs
  once per user turn and already reloads the KB. It appends one text block,
  after the KB, rendered from the store.
  - The block carries **no cache breakpoint**, because the four the API allows
    are all in use (tools, `SYSTEM_PROMPT`, KB, last message).
  - It is omitted when nothing is discoverable, and it is byte-identical when
    the discoverable set has not changed.
  - `SYSTEM_PROMPT` stays a constant built at import.
  - Cost, from sizes rather than an API run: an unchanged set is free. A change
    rewrites the block plus the history once, at the 1h write rate (2× base,
    design/82). Rebuilding `SYSTEM_PROMPT` instead would also rewrite about 9k
    tokens and the KB on every change.
- **D2 — one record per package, which follows the active release.**
  - The record is `packages/<id>/discovery.json`:
    `{enabled, decided_at, artifact_digest}`.
  - The digest is the release that was active when the user decided. It is kept
    for audit only.
  - Discovery survives an update or a rollback: prose carries no authority, and
    execution consent is pinned separately.
  - It is written with the store's atomic `_write` under the existing package
    lock. A held lock refuses at once with 409.
  - A whole-package `remove` deletes it with the directory.
  - A missing record means not discoverable. It is a user decision, not session
    state.
- **D3 — the panel only.** `POST /api/skill-packages/{id}/discovery`
  `{enabled}`.
  - The click is the recorded decision. There is no confirmation and no agent
    tool, so skill text cannot enable itself.
  - The panel says three things: discovery shows publisher text to the agent;
    it does not authorize execution; and executable packages run with the
    user's permissions, not sandboxed.
- **D4 — isolation.** Discoverable means all of these hold: discovery is
  enabled, the release is active and `eligible`, the trust policy verifies,
  and `check_release` passes.
  - A record that fails any of these is excluded, and its reason shows in the
    panel.
  - A duplicate qualified name excludes **every** record that carries it; the
    first one does not win.
  - Rendering never raises into the turn. If the store as a whole is
    unreadable, the block is omitted and the turn proceeds.
- **D5 — loading goes through `load_skill`.** A name containing `/` routes to
  `load_external_skill`, against the same discoverable set as D4.
  - The result carries publisher, version and digest.
  - The marker stays `@emits_nothing`.
- **D6 — recheck at the turn boundary.**
  - If a discovery-enabled active release reads `unchecked`, the render starts
    one background `recheck`. It is single-flight within the process, runs as
    a daemon, and is never awaited.
  - A lock that is still held is skipped again.
  - That turn excludes the release; a later turn picks it up.
  - When nothing is unchecked, the render runs no subprocess and takes no lock.
  - `retained_digests` stays `frozenset()`. That is still true, because no job
    or receipt exists until 83e-2 and 83e-3.
- **D7 — timing contract.** The render adds file reads and one policy
  verification per turn, and nothing else.
  - Measure the render at 0, 1 and N packages.
  - Tests cover the absence of unnecessary work (no subprocess, no lock) as
    well as the lines that are rendered.
- **Gate.** None; 83e-1 is local only.

**What 83e-1 settled** (PR #40):
- **Code.** `skill_store.discovery_text`, `set_discovery` and
  `load_discovered_skill`, all served by one file-only snapshot,
  `_discovery_state`, that `status()` also uses. The route is
  `POST /api/skill-packages/{id}/discovery`, and the panel carries the toggle,
  exclusion reasons and disclosure. `skill_packages._enabled_releases` isolates
  per record.
- **D7 render medians** (runner, n=30, fixture markdown packages): 0.37 ms with
  0 packages, 1.47 ms with 1, 10.9 ms with 10. That is linear, at about 1 ms per
  package. No subprocess, lock or recheck runs when nothing is unchecked.
- **Review.** Round 1 found two defects. Path-shaped names lost the built-in
  refusal, so external routing now requires a name `parse_qualified_name`
  accepts. Store isolation lived in individual test files; it is now in
  conftest. 14 of 15 decision mutants were killed; the survivor is equivalent.
- **Residuals.**
  - `package_lock` creates the package directory. A toggle that races a
    whole-package `remove` can leave an empty directory behind; the toggle
    itself refuses. `start_job` has the same shape from 83d.
  - Render cost grows by about 1 ms per package per turn.
  - `retained_digests` is still `frozenset()`; 83e-2 and 83e-3 fill it.

### 83f — publisher intake, trusted catalog and user-facing delivery

Implement the admission path specified in 83a and 83b: authenticated
submissions, signature/identity and artifact verification, supported wheel/lock
checks, and 83c conformance/self-check before promotion. Executable checks run in
disposable, restricted intake workers without production catalog credentials.
They do not run inside the trusted catalog service. Promotion is a separate
authorized step, automated under policy or held for exceptional review.
Publishers host releases; MicroClaw stores metadata and immutable references,
not copied skill sources.

Deliver an authenticated catalog with a verified local cache. Define catalog
revision/freshness handling, stale/offline disclosure and rejection of unverified
or rolled-back catalog data. Expose compatibility, publisher/support links,
install/enable state and errors in the panel. Authenticated yanks and superseding
releases affect discovery and new installation; they never remotely delete an
installed release. Revocation is surfaced separately from an ordinary yank, with
its effect on future execution defined by 83b.

Acceptance follows a fixture publisher through submit, reject/promote, verified
catalog fetch, installation and agent discovery. Include tampered submissions,
untrusted catalog updates, offline cache use, incompatible releases and yanking
an installed release. This block completes the ownership promise: ordinary
publisher releases do not require copying files or hand-maintaining pins here.

### Not a block — the SMAPpy conformance limb

It stays **NOT EXERCISED** until the publisher meets `R85`'s six preconditions,
signing included. Nothing in this notebook is scheduled around it, and no block
depends on it. If one appears to, the dependency is wrong and routes through the
fixture instead.

## What we deliberately do not build

An artifact repository of copied skill files; an `analysis` extra in
`pyproject.toml`; a `smappy` import anywhere; a frame-streaming transport; a GUI
viewer; a remote delete of an installed release; an OS sandbox for installed
workers; and any MicroClaw API that grants a community package hardware
authority. Feedback from analysis, if it is ever wanted, returns through a new
typed MicroClaw capability under the normal authorization, envelope and
write-budget rules.

## Register rows

`R82` closes when this notebook opens. `R83` closes with 83a's format tests and
83c's executable conformance tests. `R84` splits: its first item closes with
83a, its second needs nothing beyond the coverage that already exists, and its
third closes with 83e. `R85` is not ours. `R86` is disclosed by 83e's panel copy
and otherwise stays open.

## Run ledger

| Block | Branch | Start commit | Status |
|-------|--------|--------------|--------|
| notebook | `design-83-open` | `592c752` | opened 2026-09-22, PR #36 — closes `R82` |
| 83a | `block-83a` | `45a11c7` | **merged 2026-09-22** as `0629178` into `design-83-open`, PR #36 — local only, no gate |
| 83b | `block-83b` | `d0a27a9` | **merged 2026-09-23** as `5936f3b`, PR #37 — local only, no gate |
| 83c | `block-83c` | `5936f3b` | **merged 2026-09-23** as `a23b703`, PR #38 — local only, no gate; closes `R83` |
| 83d | `block-83d` | `a23b703` | **merged 2026-09-24** as `fce0188`, PR #39 — demo gate 14/14 scored from artifacts |
| 83e-1 | `block-83e-1` | `fce0188` | reviewed, PR #40 — local only, no gate |

The notebook and at least 83a land in the same pull request (operator decision,
2026-09-22). Later blocks take their own branch and PR in the usual way.
