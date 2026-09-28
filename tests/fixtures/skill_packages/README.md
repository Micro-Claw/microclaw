# Release format and TEST-ONLY trust fixtures

`markdown/` and `executable/` contain release manifests and assets. The manifest
is the authoritative external skill metadata; SKILL.md is opaque body text.
The stdlib-only `fixture_worker.runner` implements microclaw.analysis.v1,
self_check and observe_dataset. It reads lifecycle notifications, declares a
partial observation and finishes on writer completion or cancellation.
`conformance/` is a separate TEST-ONLY release of deliberately misbehaving
subprocesses; tests re-sign its intake with the committed TEST-ONLY seed.
Its pre-read stall cases use a `before-job.txt` file in the test output directory: a
worker that never reads stdin cannot select that startup behaviour from stdin.
All subsequent conformance behaviours are selected by job parameters.

The adjacent intake records are external to those release directories. Their
all-zero artifact digests are structural examples, not verified archives.
The executable lock pins the real wheel in `wheels/`, built deterministically
by `build_release.py`. That builder also creates signed release archives with
real digests and optional version overrides for installation and the demo gate.
Tests supply verified-installed records as explicit caller data; admission tests
sign records with digests computed from test bytes. Supervisor tests exercise
the reference executable without installing the dependency; store tests also
exercise real offline installation.

Both intake records are signed with Ed25519 publisher-a's TEST-ONLY key for
fixture-lab. They bind type, identity, artifact reference/digest and compatibility
metadata. The committed trust/policy-TEST-ONLY.json is signed by the TEST-ONLY
root and admits publisher-a and publisher-b, allowing rotation tests. All key
files carry TEST-ONLY in their filenames; the private seeds explicitly warn
that they are public test keys. Tests reproduce each committed signature from
the committed seed and verify the committed documents without re-signing.
The root set has environment "test". Nothing here is a production root;
MicroClaw's production root set is empty and fails closed.

smappy-0.1.0-unsigned-intake.json is an offline illustrative unsigned SMAPpy
submission, which refuses admission at signature. It requires no publisher
service or installed SMAPpy package.

Signed bytes are ASCII canonical JSON (sorted keys, separators "," and ":",
ensure_ascii=True) with the signature field removed. All pinned fixtures stay
under this directory, protected from newline translation by .gitattributes.

Manifest objects have closed fields. Asset hashes and external artifact digests
are lowercase SHA-256 hex. Locks are keyed by declared platform tags, each with
an array of `{requirement, hashes}` entries. Requirements use one unconditional
`==` version pin. Entry points are `{module: <dotted Python module>}`. Operations
are `{name, input_schema, output_schema}`; schema objects require a nonempty
`type` label, with v1 execution supporting only object types. Versions and compatibility ranges
use packaging's PEP 440 parsing. Artifact/source/issues references are HTTPS URLs.

Identifiers use `[a-z][a-z0-9]*(?:-[a-z0-9]+)*`; the qualified name is exactly
`publisher/package/skill`. No component can contain `/`. Length limits (in
characters) are 64 for publisher/package/skill identifiers, versions, digests,
operation names and platform tags; 512 for descriptions and
locked requirements; 256 for licenses, module names and version specifiers;
2048 for URLs; and 1024 for paths. Collection limits per manifest are 256 assets,
64 skills, 128 operations and 32 platforms, with 512 lock entries per platform
and 64 hashes per requirement. `microclaw.skill_packages` names these limits as
`MAX_*` constants. Length limits and single-line control rejection apply before
rendering. These formatting checks do not make publisher instructions trustworthy
or authorize execution; v1 protocol validation and supervision are exercised by test_skill_supervisor.py.

Operation schemas admit a closed JSON Schema 2020-12 subset; `type` is one of
`null`, `boolean`, `object`, `array`, `number`, `integer`, or `string`.
