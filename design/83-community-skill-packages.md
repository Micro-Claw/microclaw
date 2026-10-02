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
letting the agent read a package, and consenting to execution are distinct
recorded decisions. Since 2026-09-28 the first two are made by one click:
installing records "the agent may read it", and the panel can withdraw that
(operator decision; see 83e-2's settlement).

**Markdown-only packages also require scrutiny.** Catalog metadata is presented
to the agent; today `load_skill` returns the full skill body as a tool result,
not as system-prompt text. External skill bodies must keep that distinction and
be labelled with publisher and release provenance. Their instructions can still
influence calls to tools already authorized at real hardware. The tool layer's
refusals, envelopes, write budgets and confirmations remain the enforcement
boundary, regardless of who wrote the prose. Both package kinds therefore take
the same admission policy, and whether the agent may read one is a recorded
user decision.

## What a package can be

Three shapes, and every one of them leaves the tool layer as the enforcement
boundary.

- **Prose only** (Markdown-only kind). A skill that teaches the agent a workflow
  — a light-sheet acquisition planned from the hardware the rig has, an htSMLM
  mapping reference — with no code and no environment. Discovery (83e-1) shows
  it; `load_skill("publisher/package/skill")` returns it; the agent carries it
  out with existing tools, so it exports like any other session. **A skill can
  only describe what MicroClaw can already do**: a capability MicroClaw lacks (a
  galvo waveform locked to the camera trigger) is a first-party proposal, not a
  package.
- **A worker** (executable kind). Publisher code, out of process, over a saved
  dataset (83e-2's package route in `run_analysis_on_saved_dataset`) or a growing
  one (83e-3's trigger). A read-only observer, never a hook.
- **Prose that teaches a hook.** SKILL.md may carry hook source as text; the agent
  saves it through `generate_and_save_hook`, which lints it, shows it for review
  and pins its hash. From then on it is an ordinary saved hook and exports as
  one. It can use only MicroClaw's own environment, never the publisher's
  locked dependencies — enough for a focus metric, not for SMAPpy's fitter.

**Not a shape:** package code inside the hook runtime, or package results
steering an acquisition. The second, if wanted, is a new typed MicroClaw
capability under the normal authorization/envelope/write-budget rules — `R139`.

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
- **83e-3:** ~~trigger receipts~~ a plan-time `analysis` argument (see 83e-3
  decisions), dispatch at dataset creation, lifecycle and writer state,
  ~~the receipt half of `retained_digests`~~, the acquisition emitters'
  disclosure, and the `R86` copy.

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
    state. **Reversed 2026-09-28** (operator decision): a first install now
    writes `enabled: true, source: "install"`. The record gained `source`
    (`install` or `panel`), and a missing one now arises only from a package
    installed before this change.
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

**83e-2 decisions** (operator decisions, 2026-09-24, except where marked as
the coordinator's).

- **D1 — fold into `run_analysis_on_saved_dataset`.** ilastik is already one
  adapter name there (`BUILTIN_ADAPTERS`), so a package operation is another:
  `adapter="publisher/package:operation"` plus a required `release_digest`.
  - `axis_selection` and `input_kind` stop being schema-required; the builtin
    route refuses their absence, the package route refuses their presence, and
    likewise `calibration_ref`, `output_pixel_size_um`, `model_project_config`,
    `artifact_limits` and `max_array_bytes`.
  - Path policy is the builtin route's: `guard.resolve_readable_path` for the
    dataset, `guard.resolve_in_workspace` for `output_dir`, which must not
    exist. The tool creates it; it is the job's reserved output directory.
  - `release_digest` is looked up with `skill_store.resolve`, which never
    consults the active pointer: any ready retained release that passes
    `check_release(purpose="execution")` runs, and an update between loading the
    skill and calling never silently runs a different digest. Discovery is not
    required; consent is the gate (coordinator).
  - One process-wide analysis `Supervisor`, built lazily and closed at serve
    shutdown — never one per call, and not 83d's `self_check` supervisor
    (coordinator).
- **D2 — consent is a confirmation per call, with a session grant keyed to
  package + digest.** `CONFIRM_FN(summary, kind="analysis",
  subject="publisher/package@<digest>")`, raised only on the package route and
  before anything is created or submitted. The summary names publisher,
  package, version, digest, operation, the folded parameters, dataset, output
  directory, and "runs with your user permissions; not sandboxed".
  - `SessionGrants` gains the kind `analysis`, whose subject is a
    `publisher/package@digest` pattern rather than a fixed set. The class
    docstring gains one sentence: analysis is workflow, not self-modification.
  - An update or rollback changes the digest, so the grant stops applying.
  - A decline returns `{"status": "Analysis cancelled."}`, the shape the other
    cancelled confirmations use, and submits nothing.
  - Builtin adapters stay unprompted; ilastik is MicroClaw code running a
    program on inputs MicroClaw prepared, a package is publisher code.
- **D3 — the call returns at submission; completion is a durable record**
  (coordinator).
  - Explicit runs are for a completed dataset: the tool sends
    `acquisition {completed, writer: finished}` right after `submit`. A growing
    dataset is 83e-3's.
  - The result nests everything under `{"analysis": {...}}`: state, job ID,
    package, digest, operation, parameters, output directory, job record path.
    A refusal *before* submission (schema, eligibility, path, lock) is an
    ordinary top-level `error`, because nothing ran; a dispatch or worker
    failure *after* submission is nested, never top-level, never in a list.
  - The job record is `user_data_dir()/skill-packages/jobs/<job_id>.json`,
    written atomically at submit and at the terminal with the supervisor's
    record. It never lives in the worker's output directory. A record still
    non-terminal when serve starts reads `abandoned` (83d's `job.json` lesson).
  - One read tool, `analysis_job_status(job_id)`, `@emits_nothing`, reads the
    record and never queries a live worker. 83e-3's trigger jobs use the same
    record and tool.
  - `retained_digests` gets one source: `skill_store.retained_digests()`,
    returning the digests of non-terminal jobs; every caller that passes
    `frozenset()` today calls it instead. 83e-3 adds receipts to it.
- **D4 — parameter validation is a closed JSON Schema 2020-12 subset, with no
  new runtime dependency.** Keywords: `type` (one string), `properties`,
  `required`, `additionalProperties` (boolean), `items`, `enum`, `minimum`,
  `maximum`, `minLength`, `maxLength`, `minItems`, `maxItems`, and the
  annotations `title`, `description`, `default`.
  - Each keyword means exactly what it means in 2020-12. The traps:
    `additionalProperties` defaults to allowed; `true` is not an integer; `1.0`
    *is* an integer; `minimum`/`maximum` are inclusive; `default` is never
    applied.
  - Admission (`validate_manifest`) refuses any keyword outside the subset, in
    both `input_schema` and `output_schema`. That is what makes a later move to
    `jsonschema` backward compatible: every admitted schema means the same thing
    afterwards, and adding a keyword only loosens admission.
  - `input_schema` is enforced at call time. `output_schema` is admitted under
    the same subset and not enforced in 83e-2.
  - `jsonschema` joins the `[test]` extra only. A differential test runs both
    validators over one corpus and requires them to agree; a test asserts the
    product never imports it.
- **D5 — export: `@emits` with a comment-only renderer, for every adapter**
  (closes `R84`'s third item). The marker moves from `@emits_nothing`.
  - Builtin routes: adapter, dataset, output directory, and a statement that
    the analysis was not reproduced with how to rerun it. Package route: package
    ID, digest, operation, parameters, output directory, job record path, and
    the same statement. Existing exports start carrying this comment on purpose:
    silence about an artifact the session produced is the defect.
  - `one_line` is promoted from inside `export_session_script` (`tools.py:2270`)
    to one module-level helper that the exporter and this renderer both call.
    Every interpolated value is folded.
  - Tests: a multiline publisher failure and multiline parameters stay inside
    their comments; a session with an analysis call followed by another step
    compiles and emits that later step; a pre-submission refusal takes the
    exporter's existing skipped path.
- **D6 — timing contract** (coordinator). The package route adds, on the turn
  thread: the confirmation, `resolve` (one `check_release`, ~0.3 ms in 83c), a
  directory create and `submit` (p50 0.5 ms in 83c). It never waits on the
  worker. Tests: the tool returns while a slow fixture worker is still running,
  and the builtin route constructs no supervisor and reads no store.
- **Gate.** A demo-machine gate, added at the operator's request on
  2026-09-25 because nobody had seen the browser prompt: the banner, its session
  grant, a second release prompting under that grant, revoke, and the exported
  script — which the gate also *runs*. `design/83-block83e2-demo-gate.{py,ps1,md}`,
  built on 83d's gate plumbing; its `selftest` drives the real tool, supervisor
  and exporter and proves each limb can fail. No SMAPpy needed: the fixture
  package's `observe_dataset` is the path the prompt guards.

**What 83e-2 settled** (PR #41):
- **Code.** `completed_dataset.run_package_analysis`, behind
  `run_analysis_on_saved_dataset`'s package route, and one lazily built
  process-wide analysis `Supervisor` that serve closes at shutdown. In
  `skill_packages`: `validate_parameter_schema` and `validate_parameters`. In
  `skill_store`: job records, `_analysis_records`, `abandon_analysis_jobs` and
  `retained_digests()`. The new tool `analysis_job_status` is `@emits_nothing`.
  The tool count is now 82: 24 `@emits`, 54 `@emits_nothing`, 4 `@refuses`.
- **Consent never holds a lock.** Everything that can be checked without the
  lock runs first. The prompt then runs with no lock held. Under the lock, the
  same digest is resolved again and the trust policy is reloaded, because a
  person can take minutes to answer. A release removed while the prompt was
  open refuses.
- **A job record names its owner** (`pid` plus a per-process nonce). A sweep
  abandons a job only when its owner is dead, and retention counts only jobs
  whose owner is alive. So a second `serve` leaves the first one's jobs alone,
  and a job left by a dead CLI process stops pinning its release without a
  sweep. An unreadable record is skipped and pins nothing. PID reuse is
  accepted, as for package locks.
- **A declined call exports as a decline**
  (`{"status": ..., "cancelled": true}`), not as an analysis that was not
  reproduced.
- **D6.** On the runner, n=5, with the real supervisor and a 300 ms fixture
  worker: median 18.0 ms, max 21.6 ms. The tool returned before the worker's
  sleep ended in every sample. Mean attributed phases: resolve 6.4 ms, atomic
  writes 3.7 ms, submit 2.1 ms; about 5 ms is unattributed. Reusing one policy
  snapshot measured no meaningful change (18.4 ms before). The pre-prompt
  resolve is 6.4 ms, against 0.29 ms for `check_release` alone in 83c. Most of
  it is file reads and the record glob, which has not been measured
  separately.
- **Review.** Round 1 rejected the first turn for six defects:
  - the consent prompt held the package lock;
  - a second process abandoned the first one's live jobs;
  - one corrupt job record stopped serve starting and failed every package
    operation;
  - a declined call exported as an analysis;
  - a bare `read_text()` failed the suite-integrity check;
  - the no-import test walked a relative path, so from another directory it
    visited nothing and passed.

  All ten regression tests fail on the first turn's tree. The coordinator's
  own fix, the policy reload, was mutation-checked. Removing the
  `type`-length rows took 118 parametrized cases out of the suite, so the
  count drop is explained.
- **Gate.** The demo machine, 2026-09-28, two rounds, installed commit
  `632536e`.
  - **Round 1** stopped at the first launch. The launch message, reused from
    83d's gate, sent the operator to the package panel, which offers **Enable
    discovery**, and the runbook never mentioned it. This was a gate defect.
    Round 2 says not to open the panel, and adds a limb that scores that the
    jobs ran with no discovery record.
  - **Round 2 scored 6 of 8.** Both FAILs were the gate's fixed counts ("3
    jobs", "3 not-reproduced comments") meeting an operator slip. On turn 4 the
    operator forgot to revoke first, so an extra call ran under the grant.
    That is the grant working, not a defect.
  - **Scored from the history and the exported script, both limbs are met.**
    - Every call is accounted for: the unrevoked turn 4 was auto-approved and
      wrote `-4`. The retry after revoking reused `-4` and was **refused before
      any prompt** (the path check comes before consent). Then `-5` prompted
      and was approved plainly.
    - All 4 jobs `succeeded` with exit 0, one `final` artifact whose SHA-256
      equals that of `dataset writer finished\n`, lifecycle
      `completed`/`finished`, and one owner pid and nonce, serve's.
      `accounted_s` ranged from 0.285 to 0.851 s.
    - The export parses and carries 4 not-reproduced comments, 1 decline and 1
      `SKIPPED` for the refused retry, all four output directories and the
      digest, and no `NOT EMITTED`.
    - It exits 0 under a stub `pycromanager`, run off-rig. **Its real
      `Core()` connection on the demo machine was not exercised**, because the
      limb failed at its count before running the script. That header is
      identical to every export's, so nothing was re-run for it.
  - Consent, grant, another digest, revoke and no-discovery all passed as
    scored. The operator reported that the prompts behaved as expected.
- **Discovery is on at install** (operator decision, 2026-09-28, after round 1
  of the gate stalled at a button labelled "Enable discovery" that the operator
  could not interpret). Installing is the decision to use a package, as it is
  for browser extensions.
  - `skill_store.install` writes `{enabled: true, source: "install"}` only when
    no record exists. So the panel's "off" survives every later update,
    rollback and repair. Removing the whole package deletes the record, and a
    new install turns it on again.
  - A failed install writes nothing.
  - The panel no longer says "discovery". The button reads "Let the agent read
    this package" or "Stop the agent reading this package". The active row says
    "agent can read it" or "hidden from the agent", with any other reason
    after it.
  - The disclosure now reads: "Installing a package lets the agent read its
    instructions; you can stop that here. That never lets its code run:
    MicroClaw asks you each time, or once per session. Package code runs with
    your user permissions and is not sandboxed."
  - Two mutants were run: always overwriting the record fails the update
    assertion, and never writing it fails the first-install assertion.
  - The gate's "jobs ran without discovery" evidence still stands: it ran
    against a store with no record, before this change.
- **Residuals.**
  - The job directory grows without bound, and every retention call reads all
    of it.
  - `output_schema` is admitted but not enforced.
  - The terminal CLI never runs the startup sweep. Its dead jobs stop pinning
    through the owner rule, but they read `running` until a serve starts.

**83e-3 decisions** (operator decision, 2026-09-28).

- **D0 — no persisted consent; a plan-time argument instead.** The receipt
  exists only so that consent can outlast a session, and nothing needs that
  yet. Every tool that reaches `_acquire_with_hooks` (the one site in
  `microclaw/` that constructs an `Acquisition`) takes an `analysis` argument,
  confirmed before any hardware moves under 83e-2's `analysis` kind, so its
  session grant covers repeats. Dispatch still happens at dataset creation;
  the confirmation happens before the run starts, never during it.
  - Dropped with it: the receipt store, a creation tool, receipt panel
    controls, and the receipt half of `retained_digests()`. Live jobs already
    retain their digest.
  - The receipt design is kept, renamed **saved permission**, in `R140`.
    "Receipt" was the wrong word for it: it is a session grant that persists.

- **D1 — the argument.** `analysis={"adapter": "publisher/package:operation",
  "release_digest": ..., "parameters": {...}}`, package route only, on every
  tool that reaches `_acquire_with_hooks`: `run_zstack`, `run_timelapse`,
  `run_multiposition_acquisition`, `run_tile_acquisition`,
  `run_multiposition_with_autofocus`, `run_adaptive_survey`. One shared schema
  definition. `run_mda` does not take it; its description says why.
- **D2 — consent is 83e-2's.** Validate (shape, digest, `resolve`, operation,
  `input_schema`), then `CONFIRM_FN(kind="analysis", subject=pkg@digest)`; the
  summary says it runs on each dataset this call creates, while it grows. The
  session grant applies. No lock is held across the prompt; the digest is
  resolved again and the policy reloaded after it. The resolved release is the
  snapshot every dispatch in the call uses.
- **D3 — a decline acquires nothing**: `{"error": "Acquisition cancelled:
  analysis declined; nothing was acquired.", "cancelled": true}`. A top-level
  `error`, like every other declined acquisition, because it is the one signal
  `_recorded_outcome` reads as "nothing ran". D3 first specified a `status`,
  and the exporter rendered the declined call as a real timelapse; found by the
  gate runner before the gate existed.
- **D4 — the check runs once, in `execute_tool`, before the tool body**, so no
  acquisition tool can drop it. A later preflight refusal after an approval
  is an accepted cost; a tool that silently ignores `analysis` is not.
- **D5 — dispatch at dataset creation.** At `acquisition_construction`, with
  the collision-resolved `dataset_path`: create `<dataset>/analysis/<job_id>/`,
  take the package lock without waiting, `submit`. A held lock, a full queue
  or a refused submit is a recorded dispatch failure; the acquisition goes on.
  The initial job record is written **under that lock, before it is released**
  (foreground, before `acquire()`): it is the durable retention pin, and a
  write deferred to a thread lets `remove` or an update prune a release whose
  worker is starting — `_retention`'s own contract, found in 83e-3's review.
  Later lifecycle and terminal writes are off-thread. One job per dataset, so
  a per-position run gets N; overflow past queue + workers is recorded, and the
  confirmation says so. Nothing runs in a pycro-manager callback.
- **D6 — lifecycle.** Normal return: `completed`/`finished`. Hooked or engine
  failure: `failed`, with `finished` only if the teardown waiter did finish,
  else `unknown`. `AcquisitionUnterminated`: `unterminated`/`unknown`, then
  `notify_writer_finished` from the waiter at `acquisition_teardown_completion`.
- **D7 — recording and export.** A per-call collector read by `execute_tool`
  puts `{"analysis": {"jobs": [...]}}` on the recorded result, error results
  included: package, digest, operation, parameters, dataset, output directory,
  job ID, job record path, state, failure. Nested, so `_recorded_outcome`
  stays blind to it. The exporter's loop, not each emitter, adds a folded
  "not reproduced" comment for every recorded job, SKIPPED path included.
  Closes `R84`.
- **D8 — timing.** Zero cost without `analysis` (no store read); with it, the
  added foreground time before `acquire()` is measured, and tests assert where
  the work runs, not how long it takes.
- **D9 — gate.** Demo machine: real `_dataset_disk_location` under a collision
  suffix, the output directory's creation inside a live dataset, the fixture
  artifact only after real teardown, N jobs for a per-position run, interval
  gaps with and without `analysis`, the panel line, and the exported script
  run. The unterminated path is settled off-rig with lifecycle fixtures.

**What 83e-3 settled** (PR #42):
- **Code.** `completed_dataset.prepare_package_analysis` (validate, consent,
  re-resolve) and `submit_package_analysis` (mkdir, lock, submit, initial
  record), shared by `run_analysis_on_saved_dataset` and the new route.
  `AcquisitionAnalysisJob` owns one dataset's dispatch and lifecycle.
  `execute_tool` consumes `analysis` for the six tools in
  `tools_schema.ANALYSIS_TOOLS` and attaches `{"analysis": {"jobs": [...]}}` to
  every result, error results included. The exporter's loop writes one comment
  per job naming the rerun fields (`_JOB_DISCLOSURE_FIELDS`). `JobHandle` takes
  a caller-reserved job ID so the worker's cwd exists before it is enqueued.
  `skill_store.resolve` now skips a damaged matching install rather than
  refusing on it. The tool count is unchanged: 82.
- **Review.** Round 1 rejected the first turn for one defect and seven smaller
  findings. The defect: the initial job record, the retention pin, was written
  after the package lock was released, on the explicit route as well as the new
  one — so a `remove` or update could prune a release whose worker had just been
  queued. That came from my own D5, which said "off the foreground"; D5 is
  amended, and the test that the lock is held at the write fails both routes
  when the write is moved out. The others: a record-write failure overwrote the
  worker's failure; a malformed recorded `analysis` killed the whole export;
  `analysis: null` refused the acquisition; the prompt did not say where output
  goes; the export test did not record `analysis` in its input; a
  `BaseException` left the worker with no acquisition message and no deadline;
  the runner committed its own report.
- **A product defect found by the gate runner, before the gate existed.** A
  declined acquisition exported as a real timelapse, because D3 returned a
  `status` and `_recorded_outcome` reads only a top-level `error` as "nothing
  ran". Every other declined acquisition already returned an `error`. D3 is
  amended; the test builds its record from the real `execute_tool` decline and
  failed on the previous tree.
- **Gate.** The demo machine, 2026-09-28, one round, pinned at `c4c7a2d`. The
  exact installed commit is not in the evidence; the D3 `error` shape and the
  panel sentence in the artifacts put it at or after `c4c7a2d`. `RESULT: 0 failed or not exercised limbs /
  10; 1 operator-judged`, and every limb is met scored from the artifacts:
  - 6 tool calls for 6 turns, none retried. One decline (top-level `error`,
    `cancelled`, no construction, no dataset), one session grant, two
    auto-approvals, each before its construction (the first 361 ms before).
  - Collision: `e3movie_1`, `e3movie_2`; each job's dataset equals the
    construction diagnostic and the result's `dataset_path`. The first run was
    already suffixed (`R129`).
  - 4 jobs, 4 datasets, each output at `<dataset>\analysis\<job_id>`; frames 20,
    20, 2, 2, equal to planned and accounted. All `succeeded`,
    `completed`/`finished`, final artifact by SHA, owner the pid on port 8000.
  - The exported script's first real run against Micro-Manager exited 0 and
    wrote 20/20/20/2/2 frames: the declined timelapse was not re-acquired. Four
    not-reproduced comments, the digest only inside comments.
  - The panel sentence matched `transcript.js` exactly.
- **What the gate did not measure: analysis cost.** Frame gaps (mean 12.3 and
  14.1 ms with analysis, 13.7 ms without) and tool start to construction (331
  and 197 ms with, 396 ms without) show no difference, n=1 per arm. The fixture
  worker writes one small file and exits, on a demo camera, so this measured
  dispatch overhead and nothing about a real worker competing for CPU, memory or
  disk. The operator expects real analysis to cost something (2026-09-28);
  `R141` owns measuring it.
- **The gate's export comment was unreadable, and no limb could say so.** Each
  comment dumped the whole job record, 2,167 characters on one line, including a
  `state` snapshot reading `running` for a job that succeeded moments later.
  Fixed after the gate (operator decision): the comment names the rerun fields
  only, plus a dispatch failure. The limbs it could touch are off-rig, so no
  second round.
- **Residuals.** `R140` (saved permission) and `R141` (cost under a real
  worker). One job per dataset means a per-position run past queue + workers
  records dispatch failures; nothing has run one that large. `run_mda` still
  fires nothing (`R86`, now disclosed).

**83e-4 decisions** (operator decision, 2026-09-29). `R141`: can a worker that
actually works disturb an acquisition at all, and by how much, on the demo
machine? A demo-machine answer is a data point about that machine, not a
property of rigs.

- **D0 — no new tool, no new operation.** The loaded worker enters through
  83e-3's `analysis` argument, i.e. `prepare_package_analysis` /
  `submit_package_analysis`, which `run_analysis_on_saved_dataset`'s package
  route already shares. `open_artifact` starts no worker and is not involved.
- **D1 — `observe_dataset` observes.** The executable fixture's operation today
  reads nothing. It now tails `NDTiff.index` and reads each frame's pixels once
  as it lands, stdlib only (the worker's environment has no numpy or
  ndstorage), reader written from ndstorage's source. `parameters: {}` keeps
  today's artifacts and lifecycle. `{"cpu_threads": N, "max_s": S}` also keeps
  N threads hashing the latest frame (`hashlib` releases the GIL) until the
  writer finishes: saturation is the shape of a worker that cannot keep up, and
  an upper bound independent of the camera. `{"priority": "below_normal"}`
  lowers the worker's own priority. No added disk load: an observer reads each
  frame once. The result reports CPU time ÷ wall time, frames and bytes read,
  and the first frame index read while loaded; a loaded run whose worker was
  not loaded is NOT EXERCISED.
- **D2 — arms.** Burst (`interval_s=0`, 1000 frames at 10 ms) and spaced
  timelapse (100 frames, `interval_s=0.1`, 10 ms), each under none / loaded /
  loaded + below-normal. Per-position is out: one saturating worker already
  holds every core. Per run, over frames at or after the loaded index: the
  saved-notification gap summary, the `ElapsedTime-ms` gaps (and lateness
  against deadline, spaced), `duration_breakdown`, tool start to construction,
  frames planned against saved.
- **D3 — n=6 per cell, alternating, one discarded warm-up per type.** 83e-3's
  two same-arm runs differed by ~15%. A statistic "moved" only if all six runs
  of one cell lie beyond all six of the other (chance ≈ 0.2%); every per-run
  value is reported. ~9 GB of datasets, accepted.
- **D4 — disclosure, not a cap.** The analysis confirmation gains one generic
  line, whatever the result: *"The analysis runs at the same time as the
  acquisition, at the same priority as MicroClaw, and competes with it for CPU and disk. It is
  not throttled. Each result's frame-gap summary shows the effect."* No
  demo-machine number in the product. Making below-normal the product default is
  a separate decision, taken after the numbers. Worded "at the same priority
  as MicroClaw", not "at normal priority" (operator, 2026-09-29): Windows
  hands a child a below-normal or idle parent's class, which windows-latest CI
  showed.
- **D5 — gate.** The demo machine, as a standalone program: the real
  `execute_tool` in slot Python against the real bridge, consent answered by the
  program, MicroClaw closed. No chat turns: model latency is not the thing under
  test.
- **The demo machine is artificially quick** (operator, 2026-09-29). Its camera
  synthesizes frames in software and it has no serial devices. On M2 or M5 the
  acquisition thread waits on hardware and serial replies, and how promptly it
  is rescheduled after each wait is exactly what worker priority governs. So a
  demo null does not settle priority for a rig; that number needs M2 or M5.

**What 83e-4 settled** (PR #43):
- **Code.** The fixture's `observe_dataset` tails `NDTiff.index` (stdlib, from
  ndstorage's `Dataset.__new__` / `NDTiffIndexEntry` / `read_image`; polls at
  30 ms; partial entries retried; rollover followed) and reads each frame once.
  `cpu_threads`/`max_s` add the load, `priority` lowers its own class and
  reports the read-back. `microclaw/` changed by one disclosure line.
- **Review.** The runner's own targeted run skipped two failures the full suite
  caught (an unnamed text encoding; a `5e-324` schema minimum). windows-latest
  CI caught the third: "normal" was compared with `NORMAL_PRIORITY_CLASS`, but a
  Windows child inherits a below-normal or idle parent's class — hence D4's
  "same priority as MicroClaw". The gate review found the conditions compared
  over different frame windows (loaded from its load start, none from frame 0)
  and unqualified loaded runs feeding verdicts; both fixed before the gate.
- **Gate.** The demo machine, 2026-09-29, one round, pinned at `acdc839`:
  `RESULT: 0 failed or not exercised limbs / 8`, 42/42 runs, 12 minutes.
  Scored from the artifacts: C = 24; every loaded run's CPU ratio 18.3–23.2
  (threshold 18); read-backs 32 (normal) and 16384 (below normal); load began
  by frame 3; 1000/1000 and 100/100 frames read, bytes = frames × 524,288.
- **Measured** (n=6 per cell, repetitions 1–6, window from frame 3 / 2):

  | | none | loaded (normal) | below-normal |
  |---|---|---|---|
  | burst duration | 15.3–16.4 s | **33.9–39.8 s** | 15.2–16.3 s |
  | burst mean gap | 14.3–15.3 ms | **33.0–38.9 ms** | 14.3–15.5 ms |
  | burst p95 gap | 21 ms | **45–56 ms** | 20–21 ms |
  | spaced p95 gap | 144–157 ms | 113–115 ms | 111–114 ms |

  Loaded bursts separate from both others on every gap and duration
  statistic. Frames were produced late, not backlogged: camera
  `ElapsedTime-ms` and saved-callback means agree (33.0 vs 32.9 ms). Tool start
  to construction (0.25–1.0 s) does not separate. The spaced timelapse shows no
  cost; its idle arm was the *least* punctual, cause not attributed (`R143`).
  **A data point about the demo machine, whose camera makes frames on the CPU.**
- **Residuals.** `R142` (the rig number: dropped frames, disk, starvation
  boost), `R143`, `R144` (`duration_breakdown` has one `acquisition` phase).

### 83e-5 — package workers run at below-normal priority

**Decision** (operator, 2026-09-29, after 83e-4's measurement): the supervisor
starts every package worker at below-normal priority — Windows
`BELOW_NORMAL_PRIORITY_CLASS` at creation, POSIX `nice` — so it takes idle CPU
and yields to acquisition. Not a cap: 83e-4's below-normal worker still used
18.3–19.2 of 24 cores. It does **not** lower disk priority, so D4's line keeps
"competes with it for CPU and disk" and changes only its priority clause. The
fixture's own `priority` parameter then measures nothing new; decide whether to
keep it. Gate: a short demo check that the supervisor-set class is what the
worker reads back, and one loaded burst against none. Everything else about a
rig is `R142`.

**83e-5 decisions** (operator, 2026-09-29):
- **D1 — mechanism.** Windows: `BELOW_NORMAL_PRIORITY_CLASS` in `creationflags`
  beside `CREATE_SUSPENDED`, so it holds before any publisher code runs and
  overrides inheritance. Not a Job Object priority limit: its documentation ties
  it to `SE_INC_BASE_PRIORITY_NAME`, which elevated CI holds and a user may not.
  POSIX (test-only): prefix `nice -n k`, which sets and then execs, so there is no
  window and no `preexec_fn`. `os.setpriority(pid)` after `Popen` races the child
  and on Linux reaches only its main thread. **Never above MicroClaw**: an idle
  MicroClaw passes no flag (the worker inherits idle); POSIX
  `k = max(0, 10 - os.nice(0))`. A publisher can still change its own class, as
  it can do anything the user can; the record shows it (D3).
- **D2 — scope.** Every job, `self_check` included; no opt-out. Acquisition never
  waits on analysis, so a publisher asking for normal has nothing true to say.
  Revisit only with feedback into acquisition (`R139`).
- **D3 — evidence.** The supervisor reads the worker's class itself at its first
  message (after `nice` has exec'd) and at its terminal result, and records both
  in the job record with what it requested. A failed read records null and a
  reason and never fails the job. The worker's own report is a cross-check, not
  the record.
  **Amended after the first gate** (operator, 2026-10-01): on Windows the read
  covers every process in the worker's Job Object and records the highest
  class found and how many processes were read. A uv or stdlib venv's
  `Scripts\python.exe` is a launcher whose *child* is the worker, so the
  launched handle is not the worker: round 1's control workers read back
  `0x20` while the supervisor recorded `at_end = 0x4000` from the launcher.
  POSIX keeps the launched pid (a venv python is a symlink; `nice` execs).
- **D4 — fixture.** `priority` keeps a control: `"inherit"` (default) and
  `"normal"` (raise back). `"below_normal"` is dropped — it now measures nothing.
  Unprivileged POSIX cannot raise, so `"normal"` reports `applied: false` there.
- **D5 — disclosure.** *"The analysis runs at the same time as the acquisition,
  at below-normal CPU priority. It is not throttled, and it still competes with
  the acquisition for CPU and disk."* The old last sentence is dropped: a single
  result's gap summary cannot show an effect without a no-analysis baseline.
  "Disk" stays: Windows' priority class does not lower I/O priority.
- **D6 — gate.** 83e-4's program, burst only, three cells — none, loaded at the
  supervisor's class, loaded with the fixture's `"normal"` control — n=4 plus a
  warm-up (13 runs). "Every control run beyond every supervisor-loaded run" has
  chance 2/70 ≈ 2.9%; supervisor-loaded against none is reported as ranges, not
  scored as equality. CI settles class, inheritance, cap and read-back on both
  platforms through the real supervisor.

**What 83e-5 settled** (PR #44):
- **Code.** `skill_supervisor._run` adds `BELOW_NORMAL_PRIORITY_CLASS` to the
  suspended launch (none when MicroClaw is idle) or prefixes `nice -n k`. The
  job record's `priority` holds `requested`, `inherited`, `at_start`, `at_end`,
  `processes_read` and per-phase `reason`. On Windows each sample reads every
  Job Object member (≤ 64) and keeps the highest class by scheduling rank; the
  query handles are held until the job closes, so the terminal sample still
  reads a worker that has exited. The fixture's `priority` is `inherit` /
  `normal`. 83e-4's gate program now targets a fixture parameter that no longer
  exists: it is a record; rerun its measurement with 83e-5's.
- **Review.** Four revision rounds. Two came from the coordinator's diff reading
  (a 2 s exit-path join; `requested` in two shapes) and two from the gate (below).
- **Gate round 1** (demo machine, 2026-10-01, pinned `1b12372`): 8/8, but the
  raw job records contradicted D3. All four `normal` controls' workers read back
  `0x20` while the supervisor recorded `at_end = 0x4000`: the slot's
  `env-a\Scripts\python.exe` is a uv launcher, so `Popen`'s handle was the
  launcher and the worker its child. Loaded runs only agreed through
  inheritance. CI could not see it because it launches a real `python.exe`;
  limb 5 passed because it compared the control with the worker only. Hence
  D3's amendment, a stdlib-venv launcher test in CI, and a limb that requires
  the control's `at_end` to equal its read-back. The first fix read the job's
  pid list and would have lost `at_end` to a worker that exits within the
  monitor's 10 ms poll; its test's rendezvous hid that, so the handles are now
  retained.
- **Gate round 2** (demo machine, 2026-10-01, pinned `c6f3a56`, 13/13 runs,
  ~5 min): `RESULT: 0 failed or not exercised limbs / 8`. Scored from the job
  records: MicroClaw `0x20`, nothing inherited. Every loaded run recorded
  `0x4000` at both samples, equal to the worker's read-back. Every control
  recorded `0x20` at both, equal to its read-back; it raises itself before its
  first message, so `at_start` already sees it. `processes_read` = 3 at both
  samples in all 8 jobs: the launcher and the interpreter account for two, and
  the third is **not attributed**. CPU ratio 18.4–18.5 loaded, 19.4–19.6
  control. Load began by frame 3, and every job read 1000/1000 frames,
  524,288,000 bytes.
- **Measured** (round 2, n=4 per cell, from frame 3):

  | | none | loaded (supervisor's class) | normal control |
  |---|---|---|---|
  | burst duration | 15.66–16.29 s | 15.36–16.07 s | **32.62–34.31 s** |
  | burst mean gap | 15.0–15.3 ms | 14.3–15.1 ms | **31.7–33.4 ms** |
  | burst p95 gap | 21 ms | 21 ms | **45–48 ms** |

  The control separates from the loaded cell on every gap and duration
  statistic in both rounds (all 4 beyond all 4; chance 2/70 ≈ 2.9%). Round 1
  agrees: 15.14–16.28 s loaded against 31.82–33.60 s control. Loaded against
  none is ranges only, and nothing here claims they are equal. **A data point
  about the demo machine, whose camera makes frames on the CPU.**
- **Not shown.** The new Windows tests were never watched failing on the
  pre-fix code; CI ran only the fixed tree. Round 1's artifacts are the evidence
  that the defect existed. The rig question is still `R142`.

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

**83f decisions** (operator, 2026-10-01).

- **D1 — four blocks.** 83f-1: the signed catalog, its verified local cache,
  freshness, rollback refusal and offline/stale state. 83f-2: panel delivery —
  install and update from the catalog, the production index route
  (`find_links=None`) run for the first time, compatibility, support links,
  withdrawn and blocked rows. 83f-3: release intake into the catalog repository.
  83f-4: the production root, the first production policy, and the end-to-end
  path on the demo machine. Only 83f-4 waits on the key; 83f-1–3 use test roots.
- **D2 — MicroClaw vouches for publishers, not packages.** The goal is that a
  publisher, not MicroClaw, maintains their package. The root signs only the
  policy: admitted publishers and their keys, and blocks. Each release is signed
  by its publisher's key (83b), and an admitted publisher's release enters the
  catalog **without operator action**. The panel says the package is the
  publisher's: *"MicroClaw does not test or support this package. Report
  problems to <publisher>"*, with the manifest's `issues_url`. Compatibility
  breakage across a MicroClaw update stays 83d's disabled-and-why, fixed by the
  publisher's next release.
  **This removes the pre-promotion conformance run** from §83f: running each
  release in a MicroClaw-operated worker is what would make the catalog read as
  tested. Intake checks signature, admitted publisher, manifest and lock
  structure, and that the published artifact's bytes match the signed digest.
  It runs no publisher code. The release's `self_check` still runs at install
  on the user's machine (83d), so a release that cannot start cannot be
  installed. The operator keeps two duties: admitting a publisher once, and
  blocking a publisher, key or release.
- **D3 — key custody.** One offline root held by the operator, with a spare
  root kept separately; both are in `PRODUCTION_ROOTS` (verification already
  accepts any listed root). Used a few times a year. Policy expiry is long,
  about six months, with a reminder before it lapses. A lapsed policy pauses
  new installs and repairs; installed releases keep running (83b). Losing both
  roots is recovered by a MicroClaw update carrying new ones.
- **D4 — the catalog lives in a new public repository**,
  `Micro-Claw/package-catalog`, created when 83f-3 needs it. Publishers add
  releases there; artifacts stay on the publishers' own hosts and are pinned by
  digest. Clients need no credentials.
- **D5 — withdrawn vs blocked, as the panel says it.** Never "yanked".
  - *"Withdrawn by its publisher: <reason>. It stays installed and keeps
    working; new installs can't choose it."* plus any newer version on offer.
  - *"Blocked by MicroClaw: <reason>. The agent can't read it and it can't run.
    Nothing was deleted — its files and every result it produced are still on
    this computer."* with install-newer and remove. A job already running
    finishes. `revoked_releases` gains a reason.
  - Package updates are never automatic.
- **D6 — the agent may search the catalog** (reverses §83e's "remote catalog
  entries remain absent until the user acts"). The search returns short cards
  from admitted publishers only: qualified name, publisher, one-line
  description, compatibility. They carry 83a's metadata bounds and are labelled
  as publisher text. It never returns a skill body. **Installing stays a panel
  click**; no agent route installs. Which block carries the search tool is
  decided with 83f-1/2.

- **D7 — five blocks, not four** (operator, 2026-10-01, after 83f-1). The
  agent's catalog search (D6) is its own block, **83f-2**, before panel
  delivery. It reads only 83f-1's `catalog_entries`, needs no demo machine, and
  is the one change that decides what publisher text reaches the agent, so it
  is reviewed alone. Panel delivery becomes **83f-3**, release intake
  **83f-4**, production root and the end-to-end path **83f-5**. 83f-3's demo
  gate then covers the whole journey: the agent finds a package, the user
  installs it from the panel, and the agent can read it.

Found while checking the tree: `store_trust_policy` verifies the cached
previous policy against the *current* roots before accepting a new one, so
after a root rotation — which ships in a MicroClaw update — every refresh
refuses for good. Unreachable until 83f adds a fetch; 83f-1 fixes it.

**What 83f-1 settled** (`microclaw/skill_packages.py`, `microclaw/skill_store.py`,
`updates._open_manual`; tests in `tests/test_skill_store.py`):
- **Two documents from one base URL.** `policy.json` is the only root-signed
  document; `catalog.json` is an unsigned envelope of individually
  publisher-signed releases and withdrawals (`microclaw.skill-withdrawal.v1`).
  Each entry stands alone: a bad one is excluded with a field-named reason, and
  differing copies of one digest exclude every copy.
- **The cache is a monotonic union, and that is the rollback refusal.** Nothing
  accepted is forgotten because a later catalog omits it, and a withdrawal is
  permanent. Every read re-verifies every entry against the policy loaded now.
  Compatibility, installed, withdrawn and blocked are computed at read time and
  never stored.
- **A withdrawal applies only to the release it names in full** — publisher,
  package, version and digest. Round 1 matched on digest alone, so one admitted
  publisher could withdraw another's release.
- **The intake record carries the listing card** (`license`, `source_url`,
  `issues_url`, `skills[{name, description}]`), each bound to the manifest at
  install. Blocks carry a `reason`.
- **No fetch while production roots are empty**; the state reads `unpublished`.
  Test roots may name a `catalog_url`. The fetch reuses the updater's opener
  with a host set and a label, so the updater's messages are unchanged and the
  catalog never says "repository is not public". Serve's startup thread
  refreshes once, even if the package recheck before it fails.
- **The policy store survives a root rotation.** A cached policy the current
  roots no longer verify keeps its revision as a floor (strictly greater
  required); an unreadable or other-environment one sets no floor.
- **`status()` reports fetch state only** (`unpublished`, `never_fetched`, `ok`,
  `unreachable`, `refused`): one policy verification, shared with discovery,
  and no catalog read. Round 1 verified every entry on every panel poll, which
  was my C8. Measured (runner, n=30): 0.28 ms at 0 and at 1000 cached releases.
  `catalog_entries()` verifies one signature per entry: medians 0.33, 0.54, 18.4
  and 180 ms at 0, 1, 100 and 1000 (n=12). The per-turn discovery render reads
  no catalog file and makes no request.
- **Not exercised:** a real fetch. `Micro-Claw/package-catalog` does not exist
  and no root does, so every fetch in this block is a fake opener. The first
  real fetch is 83f-3's gate (renumbered by D7).
- **Review.** One start turn, one revision with seven findings: the withdrawal
  identity; `status()` cost; refused data reading as offline; a second copy of
  the compatibility check; failed installs counted as installed; startup
  ordering; a test without a text encoding (the coordinator's full suite).
  Runner mutations 25/25 killed; the coordinator re-ran the digest-only
  withdrawal mutant (3 identity tests fail).

**83f-2 decisions** (operator, 2026-10-01). One agent tool, `@emits_nothing`,
reading only `catalog_entries`; local only, no gate.

- **E1 — the saved copy only.** A search never fetches. It reads the local
  cache and says how old it is, or that there is none (`unpublished`,
  `never_fetched`, `unreachable`, `refused` from `status()`).
- **E2 — the newest installable version per skill.** Withdrawn and blocked
  releases never appear. Releases that do not fit this build or platform do
  appear, with their compatibility reason.
- **E3 — one line, at most 200 characters.** Whitespace in the publisher's
  description collapses to single spaces and the text is cut at 200 with `…`.
  The panel keeps the full text.
- **E4 — every word must match, at most 10 cards.** Case-insensitive, over
  qualified name, publisher and description. The result gives the total
  matched; an empty query lists the first 10 and the total.
- **E5 — each card names the next step.** Not installed: install from the
  Skills panel. Installed but not enabled for discovery: enable it in the
  panel. Enabled: `load_skill` by qualified name. No card carries a skill body,
  and no route installs.

**What 83f-2 settled** (`skill_store.search_catalog`, tool
`search_skill_catalog`; tests in `tests/test_skill_store.py`):
- **The search reads files only.** One discovery snapshot, one
  `catalog_entries` read, `_catalog_status` with the snapshot's policy. Tests
  fail any request and any `refresh_catalog`. One search verifies each release
  and withdrawal signature once.
- **"Load it" comes from the loader.** `enabled` is decided by calling
  `load_external_skill` on the snapshot's own candidates, for returned cards
  only (at most 10), and the text is discarded. Round 1 re-implemented the
  loader's asset and UTF-8 checks by hand, a second copy that would have drifted.
- **The installed-but-not-loadable instruction says why.** "Turn it on" only
  when the package's discovery decision is off or missing; otherwise "It is
  installed, but the agent can't load it. The Skills panel shows why." That
  covers a broken install and an installed version that lacks the card's skill.
  Round 1 told the user to turn on something already on.
- **A damaged install record is skipped, not fatal.** Round 1 failed the whole
  search on one `ready` record with no intake.
- **The query is bounded at 512 characters** (the description bound) and
  refuses past it rather than truncating.
- **Review.** One start turn, one revision with three findings (C1–C3). The
  coordinator's mutants: 8/8 killed (withdrawn shown, enabled without loading,
  unguarded intake, constant "turn it on", no 200-character cut, version-only
  ranking, no 10-card limit, any-word matching); control 12/12.
- **Not exercised:** a real catalog. As in 83f-1, every catalog here is a
  fixture; the first real one is 83f-3's gate.

**83f-3 decisions** (operator, 2026-10-01). Panel delivery: download,
install, update and remove from the catalog, with a demo gate covering the
whole journey (D7).

- **G1 — download from any https host.** The signed digest is what admits the
  artifact, so no host list is kept. The download carries a size cap and a
  timeout; a digest mismatch discards the file.
- **G2 — the panel lists everything, installed first.** Then every other
  package from an admitted publisher at its newest version. Incompatible ones
  show greyed with their reason.
- **G3 — newest version only, plus rollback.** One button per row: "Install
  <v>", or "Update to <v>" over an older install. Going back is the existing
  rollback. Same rule as E2.
- **G4 — refresh at startup and on a "Check now" button.** The panel says when
  the catalog was last checked, or that it is offline and showing the saved
  copy. No background checks, and install does not refetch.
- **G5 — the gate's catalog lives on this block's branch.** A test-root-signed
  policy, catalog and package in `design/`, fetched from
  `raw.githubusercontent.com` (the production catalog host), with the
  package's locked wheels from PyPI: the first real run of `find_links=None`.
  `Micro-Claw/package-catalog` is still created by intake (83f-4). The gate
  restores the machine's trust roots afterwards.
- **G6 — Remove on every installed package**, behind a confirm step that says
  only the package's installed files go and its results stay. Refused while an
  analysis using it is running.
- **G7 — Install and Update confirm first.** Package and version, publisher,
  licence, D2's notice with the issues link, Install / Cancel. Cancel downloads
  nothing.
- **G8 — after the first demo gate's screenshot** (operator, 2026-10-02).
  Buttons are plain verbs: Install, Update, Remove. The version lives on the
  row, which reads "<installed> — <newer> available" when an update is offered,
  and the confirm box names the version it installs. This amends G3's button
  text. D2's notice appears **once above the list** ("MicroClaw does not test or
  support these packages. Each one is its publisher's."), and each row links
  "Source" and "Report problems to <publisher>". The full D2 sentence stays in
  the Install/Update confirm box.
- **G9 — one box per package** (operator, 2026-10-02, after round 2: the
  tampered install's error appeared in a separate list at the bottom of the
  panel and was missed). Each package's card carries its install state, the
  agent-access toggle and its last action's result or error. The separate list
  goes away; an installed package absent from the catalog gets a card of its
  own. An install job's result belongs to the card of the publisher whose
  release was requested.

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
| 83e-1 | `block-83e-1` | `fce0188` | **merged 2026-09-24** as `2a1c42a`, PR #40 — local only, no gate |
| 83e-2 | `block-83e-2` | `2a1c42a` | **merged 2026-09-28** as `3dde106`, PR #41 — demo gate 2026-09-28, 6/8 as scored, both FAILs the gate's fixed counts, artifacts meet them; closes `R84`'s third item |
| 83e-3 | `block-83e-3` | `3dde106` | **merged 2026-09-29** as `6b4d11b`, PR #42 — demo gate 2026-09-28, 10/10 scored from artifacts, 1 operator-judged; closes `R84` |
| 83e-4 | `block-83e-4` | `6b4d11b` | **merged 2026-09-29** as `f3d039c`, PR #43 — demo gate 2026-09-29, 8/8 scored from artifacts; closes `R141`, opens `R142`–`R144` |
| 83e-5 | `block-83e-5` | `f3d039c` | **merged 2026-10-01** as `17e4514`, PR #44 — demo gate 2026-10-01, round 1 8/8 with a priority-record defect found in the job records, round 2 8/8 scored from artifacts |
| 83f | — | `17e4514` | opened 2026-10-01 — decisions D1–D6 taken; split into 83f-1…83f-4 |
| 83f-1 | `block-83f-1` | `a7261f5` | **merged 2026-10-01** as `225b6f2`, PR #45 — local only, no gate |
| 83f-2 | `block-83f-2` | `225b6f2` | **merged 2026-10-01** as `9e9271a`, PR #46 — local only, no gate; decisions E1–E5 |
| 83f-3 | `block-83f-3` | `9e9271a` | opened 2026-10-01, PR #47 — panel delivery; decisions G1–G7; demo gate pending |

The notebook and at least 83a land in the same pull request (operator decision,
2026-09-22). Later blocks take their own branch and PR in the usual way.
