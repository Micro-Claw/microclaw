# Format fixtures only

`markdown/` and `executable/` contain release manifests and assets. The manifest
is the authoritative external skill metadata; SKILL.md is opaque body text.
No runner or protocol transcript is supplied. Block 83c supplies the declared
`fixture_worker.runner` alongside the protocol it implements.

The adjacent intake records are external to those release directories. Their
all-zero artifact digests and the executable lock's illustrative hash are
structural examples, not verified artifacts or installable dependencies. Tests
supply verified-installed records as explicit caller data; they do not perform
admission. No fixture claims signature or executable protocol conformance.

Manifest objects have closed fields. Asset hashes and external artifact digests
are lowercase SHA-256 hex. Locks are keyed by declared platform tags, each with
an array of `{requirement, hashes}` entries. Requirements use one unconditional
`==` version pin. Entry points are `{module: <dotted Python module>}`. Operations
are `{name, input_schema, output_schema}`; schema objects require a nonempty
`type` label, whose semantics are left to 83c. Versions and compatibility ranges
use packaging's PEP 440 parsing. Artifact/source/issues references are HTTPS URLs.

Identifiers use `[a-z][a-z0-9]*(?:-[a-z0-9]+)*`; the qualified name is exactly
`publisher/package/skill`. No component can contain `/`. Length limits (in
characters) are 64 for publisher/package/skill identifiers, versions, digests,
operation names, platform tags and schema type labels; 512 for descriptions and
locked requirements; 256 for licenses, module names and version specifiers;
2048 for URLs; and 1024 for paths. Collection limits per manifest are 256 assets,
64 skills, 128 operations and 32 platforms, with 512 lock entries per platform
and 64 hashes per requirement. `microclaw.skill_packages` names these limits as
`MAX_*` constants. Length limits and single-line control rejection apply before
rendering. These formatting checks do not make publisher instructions trustworthy
or authorize execution; schema semantics and protocol support remain 83c's work.
